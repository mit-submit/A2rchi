from typing import Any, Callable, Dict, List, Optional, Sequence, Iterator, AsyncIterator

from langchain.agents import create_agent
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph

from src.a2rchi.pipelines.agents.utils.prompt_utils import read_prompt
from src.a2rchi.utils.output_dataclass import PipelineOutput
from src.a2rchi.pipelines.agents.utils.document_memory import DocumentMemory
from src.utils.logging import get_logger

logger = get_logger(__name__)


class BaseAgent:
    """
    BaseAgent provides a foundational structure for building pipeline classes that
    process user queries using configurable language models and prompts.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        self.config = config
        self.a2rchi_config = self.config["a2rchi"]
        self.dm_config = self.config["data_manager"]
        self.pipeline_config = self.a2rchi_config["pipeline_map"][self.__class__.__name__]
        self._active_memory: Optional[DocumentMemory] = None
        self._static_tools: Optional[List[Callable]] = None
        self._active_tools: List[Callable] = []
        self.agent: Optional[CompiledStateGraph] = None
        self.agent_llm: Optional[Any] = None
        self.agent_prompt: Optional[str] = None

        self._init_llms()
        self._init_prompts()

        if self.agent_llm is None:
            if not self.llms:
                raise ValueError(f"No LLMs configured for agent {self.__class__.__name__}")
            self.agent_llm = self.llms.get("chat_model") or next(iter(self.llms.values()))
        if self.agent_prompt is None:
            self.agent_prompt = self.prompts.get("agent_prompt")

    def create_document_memory(self) -> DocumentMemory:
        """Instantiate a fresh document memory for an agent run."""
        return DocumentMemory()

    def start_run_memory(self) -> DocumentMemory:
        """Create and store the active memory for the current run."""
        memory = self.create_document_memory()
        self._active_memory = memory
        return memory

    @property
    def active_memory(self) -> Optional[DocumentMemory]:
        """Return the memory currently associated with the run, if any."""
        return self._active_memory

    def finalize_output(
        self,
        *,
        answer: str,
        memory: Optional[DocumentMemory] = None,
        messages: Optional[Sequence[BaseMessage]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        final: bool = True,
    ) -> PipelineOutput:
        """Compose a PipelineOutput from the provided components."""
        documents = memory.unique_documents() if memory else []
        return PipelineOutput(
            answer=answer,
            source_documents=documents,
            messages=messages or [],
            metadata=metadata or {},
            final=final,
        )

    def invoke(self, **kwargs) -> PipelineOutput:
        """Synchronously invoke the agent graph and return the final output."""
        logger.debug("Invoking %s", self.__class__.__name__)
        agent_inputs = self._prepare_agent_inputs(**kwargs)
        if self.agent is None:
            self.refresh_agent(force=True)
        logger.debug("Agent refreshed, invoking now")
        answer_output = self.agent.invoke(agent_inputs, {"recursion_limit": 50})
        logger.debug("Agent invocation completed")
        messages = self._extract_messages(answer_output)
        metadata = self._metadata_from_agent_output(answer_output)
        output = self._build_output_from_messages(messages, metadata=metadata)
        return output

    def stream(self, **kwargs) -> Iterator[PipelineOutput]:
        """Stream agent updates synchronously."""
        logger.debug("Streaming %s", self.__class__.__name__)
        agent_inputs = self._prepare_agent_inputs(**kwargs)
        if self.agent is None:
            self.refresh_agent(force=True)

        latest_messages: List[BaseMessage] = []
        for event in self.agent.stream(agent_inputs, stream_mode="updates"):
            messages = self._extract_messages(event)
            if messages:
                latest_messages = messages
                content = self._message_content(messages[-1])
                if content:
                    yield self.finalize_output(
                        answer=content,
                        memory=self.active_memory,
                        messages=messages,
                        metadata={},
                        final=False,
                    )
        yield self._build_output_from_messages(latest_messages)

    async def astream(self, **kwargs) -> AsyncIterator[PipelineOutput]:
        """Stream agent updates asynchronously."""
        logger.debug("Streaming %s asynchronously", self.__class__.__name__)
        agent_inputs = self._prepare_agent_inputs(**kwargs)
        if self.agent is None:
            self.refresh_agent(force=True)

        latest_messages: List[BaseMessage] = []
        async for event in self.agent.astream(agent_inputs, stream_mode="updates"):
            messages = self._extract_messages(event)
            if messages:
                latest_messages = messages
                content = self._message_content(messages[-1])
                if content:
                    yield self.finalize_output(
                        answer=content,
                        memory=self.active_memory,
                        messages=messages,
                        metadata={},
                        final=False,
                    )
        yield self._build_output_from_messages(latest_messages)

    def _init_llms(self) -> None:
        """Initialise language models declared for the pipeline."""

        model_class_map = self.a2rchi_config["model_class_map"]
        models_config = self.pipeline_config.get("models", {})
        self.llms: Dict[str, Any] = {}

        all_models = dict(models_config.get("required", {}), **models_config.get("optional", {}))
        initialised_models: Dict[str, Any] = {}

        for model_name, model_class_name in all_models.items():
            if model_class_name in initialised_models:
                self.llms[model_name] = initialised_models[model_class_name]
                logger.debug(
                    "Reusing initialised model '%s' of class '%s'",
                    model_name,
                    model_class_name,
                )
                continue

            model_entry = model_class_map[model_class_name]
            model_class = model_entry["class"]
            model_kwargs = model_entry["kwargs"]
            instance = model_class(**model_kwargs)
            self.llms[model_name] = instance
            initialised_models[model_class_name] = instance

    def _init_prompts(self) -> None:
        """Initialise prompts defined in pipeline configuration."""

        prompts_config = self.pipeline_config.get("prompts", {})
        required = prompts_config.get("required", {})
        optional = prompts_config.get("optional", {})
        all_prompts = {**optional, **required}

        self.prompts: Dict[str, SystemMessage] = {}
        for name, path in all_prompts.items():
            if not path:
                continue
            try:
                prompt_template = read_prompt(path)
            except FileNotFoundError as exc:
                if name in required:
                    raise FileNotFoundError(
                        f"Required prompt file '{path}' for '{name}' not found: {exc}"
                    ) from exc
                logger.warning(
                    "Optional prompt file '%s' for '%s' not found or unreadable: %s",
                    path,
                    name,
                    exc,
                )
                continue
            self.prompts[name] = str(prompt_template) # TODO at some point, make a validated prompt class to check these?

    def rebuild_static_tools(self) -> List[Callable]:
        """Recompute and cache the static tool list."""
        self._static_tools = list(self._build_static_tools())
        return self._static_tools

    @property
    def tools(self) -> List[Callable]:
        """Return the cached static tools, rebuilding if necessary."""
        if self._static_tools is None:
            return self.rebuild_static_tools()
        return list(self._static_tools)

    @tools.setter
    def tools(self, value: Sequence[Callable]) -> None:
        """Explicitly set the static tools cache."""
        self._static_tools = list(value)

    def refresh_agent(
        self,
        *,
        static_tools: Optional[Sequence[Callable]] = None,
        extra_tools: Optional[Sequence[Callable]] = None,
        force: bool = False,
    ) -> CompiledStateGraph:
        """Ensure the LangGraph agent reflects the latest tool set."""
        base_tools = list(static_tools) if static_tools is not None else self.tools
        toolset: List[Callable] = list(base_tools)
        if extra_tools:
            toolset.extend(extra_tools)

        requires_refresh = (
            force
            or self.agent is None
            or len(toolset) != len(self._active_tools)
            or any(a is not b for a, b in zip(toolset, self._active_tools))
        )
        if requires_refresh:
            logger.debug("Refreshing agent %s with %d tools", self.__class__.__name__, len(toolset))
            self.agent = self._create_agent(toolset)
            self._active_tools = list(toolset)
        return self.agent

    def _create_agent(self, tools: Sequence[Callable]) -> CompiledStateGraph:
        """Create the LangGraph agent with the specified LLM, tools, and system prompt."""
        logger.debug(f"Creating agent {self.__class__.__name__} with {len(tools)} tools")
        return create_agent(
            model=self.agent_llm,
            tools=tools,
            system_prompt=self.agent_prompt,
        )

    def _build_static_tools(self) -> List[Callable]:
        """Build and returns static tools defined in the config."""
        return []

    def _prepare_agent_inputs(self, **kwargs) -> Dict[str, Any]:
        """Subclasses must implement to provide agent input payloads."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement _prepare_agent_inputs")

    def _metadata_from_agent_output(self, answer_output: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for subclasses to enrich metadata returned to callers."""
        return {}

    def _extract_messages(self, payload: Any) -> List[BaseMessage]:
        """Pull LangChain messages from a stream/update payload."""
        if isinstance(payload, dict):
            messages = payload.get("messages")
            if isinstance(messages, list) and all(isinstance(msg, BaseMessage) for msg in messages):
                return messages
        return []

    def _message_content(self, message: BaseMessage) -> str:
        """Normalise message content to a printable string."""
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        return str(content)

    def _format_message(self, message: BaseMessage) -> str:
        """Condense a message for logging/metadata storage."""
        role = getattr(message, "type", message.__class__.__name__)
        content = self._message_content(message)
        if len(content) > 400:
            content = f"{content[:397]}..."
        return f"{role}: {content}"

    def _build_output_from_messages(
        self,
        messages: Sequence[BaseMessage],
        *,
        metadata: Optional[Dict[str, Any]] = None,
        final: bool = True,
    ) -> PipelineOutput:
        """Create a PipelineOutput from the agent's message history."""
        if messages:
            answer_text = self._message_content(messages[-1]) or "No answer generated by the agent."
        else:
            answer_text = "No answer generated by the agent."
        safe_metadata = dict(metadata or {})
        return self.finalize_output(
            answer=answer_text,
            memory=self.active_memory,
            messages=messages,
            metadata=safe_metadata,
            final=final,
        )
