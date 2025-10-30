from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.documents import Document
from langgraph.graph.state import CompiledStateGraph
from langchain.agents import create_agent

from src.utils.logging import get_logger
from src.a2rchi.pipelines.agents.base import BaseAgent
from src.a2rchi.utils.output_dataclass import PipelineOutput
from src.data_manager.vectorstore.retrievers import SemanticRetriever
from src.a2rchi.pipelines.agents.tools import (
    create_file_search_tool,
    create_metadata_search_tool,
    create_retriever_tool,
)
from src.data_manager.collectors.utils.index_utils import CatalogService

logger = get_logger(__name__)


class CMSCompOpsAgent(BaseAgent):
    """Agent designed for CMS CompOps operations."""

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(config, *args, **kwargs)
        self.agent_llm = self.llms.get("chat_model") or next(iter(self.llms.values()))
        self.agent_prompt = self.prompts.get("agent_prompt")

        data_path = self.config["global"]["DATA_PATH"]
        self.catalog_service = CatalogService(data_path=data_path) # TODO I don't think we want to define this here, can be at tool level

        self._vector_retriever = None
        self._vector_tool = None
        self._active_tools: Optional[List[Callable]] = None

        self.tools = self._build_static_tools()
        self.agent = self._create_agent(self.tools)

    def _create_agent(self, tools: Sequence[Callable]) -> CompiledStateGraph:
        """Create the LangGraph agent with the specified LLM, tools, and system prompt."""
        logger.debug("Creating CMSCompOpsAgent with %d tools", len(tools))
        return create_agent(
            model=self.agent_llm,
            tools=tools,
            system_prompt=self.agent_prompt,
        )

    def _build_static_tools(self) -> List[Callable]:
        """Initialise static tools that are always available to the agent."""
        configured_tools = list(self.pipeline_config.get("tools", []))
        file_search_tool = create_file_search_tool(
            self.catalog_service,
            description="Search local source files collected in the catalog.",
            store_docs=self._store_documents,
        )
        metadata_search_tool = create_metadata_search_tool(
            self.catalog_service,
            description="Search metadata associated with local source files.",
            store_docs=self._store_documents,
        )
        return configured_tools + [file_search_tool, metadata_search_tool]

    def _store_documents(self, stage: str, docs: Sequence[Document]) -> None:
        """Centralised helper used by tools to record documents into the active collector."""
        collector = self.active_collector
        if not collector:
            return
        # Prefer collector convenience method if available
        try:
            collector.record_documents(stage, docs)
        except Exception:
            # fallback to explicit record + note
            collector.record(stage, docs)
            collector.note(f"{stage} returned {len(list(docs))} document(s).")

    def _infer_speaker(self, speaker: str) -> type[BaseMessage]:
        """Infer the speaker type and return the appropriate message class."""
        if speaker.lower() in ["user", "human"]:
            return HumanMessage
        if speaker.lower() in ["agent", "ai", "assistant", "a2rchi"]:
            return AIMessage
        logger.warning("Unknown speaker type: %s. Defaulting to HumanMessage.", speaker)
        return HumanMessage

    def _prepare_inputs(self, history: Any, **kwargs) -> Dict[str, Any]:
        """Create list of messages using LangChain's formatting."""
        history = history or []
        history_messages = [self._infer_speaker(msg[0])(msg[1]) for msg in history]
        return {"history": history_messages}

    def _prepare_agent_inputs(self, **kwargs) -> Dict[str, Any]:
        """Prepare agent state and formatted inputs shared by invoke/stream."""
        collector = self.start_run_collector()
        vectorstore = kwargs.get("vectorstore")
        if vectorstore:
            self._update_vector_retriever(vectorstore)
        else:
            self._vector_retriever = None
            self._vector_tool = None

        # ensure the latest files are indexed for the tools' use
        self.catalog_service.refresh()
        collector.note("Catalog refreshed for agent run.")

        toolset = list(self.tools)
        if self._vector_tool:
            toolset.append(self._vector_tool)

        if (
            self._active_tools is None
            or len(toolset) != len(self._active_tools)
            or any(a is not b for a, b in zip(toolset, self._active_tools))
        ):
            logger.debug("Refreshing agent graph with %d tools", len(toolset))
            self.agent = self._create_agent(toolset)
            self._active_tools = list(toolset)

        inputs = self._prepare_inputs(history=kwargs.get("history"))
        history_messages = inputs["history"]
        if history_messages:
            collector.note(f"History contains {len(history_messages)} message(s).")
            last_message = history_messages[-1]
            content = getattr(last_message, "content", "")
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            if content:
                snippet = content if len(content) <= 200 else f"{content[:197]}..."
                collector.note(f"Latest user message: {snippet}")
        return {"messages": history_messages}

    def invoke(self, **kwargs) -> PipelineOutput:
        logger.debug("Invoking CMSCompOpsAgent")

        agent_inputs = self._prepare_agent_inputs(**kwargs)

        answer_output = self.agent.invoke(agent_inputs)
        logger.debug("Agent invocation completed")

        messages = answer_output.get("messages", [])
        metadata = {"agent_output_keys": sorted(answer_output.keys())}
        return self._build_output_from_messages(messages, metadata=metadata)

    def stream(self, **kwargs) -> Iterator[PipelineOutput]:
        """Stream agent updates synchronously."""
        logger.debug("Streaming CMSCompOpsAgent")
        agent_inputs = self._prepare_agent_inputs(**kwargs)
        latest_messages: List[BaseMessage] = []

        for event in self.agent.stream(agent_inputs, stream_mode="updates"):
            messages = self._extract_messages(event)
            if messages:
                latest_messages = messages
                content = self._message_content(messages[-1])
                if content:
                    yield self.finalize_output(
                        answer=content,
                        collector=self.active_collector,
                        metadata={"event": self._summarize_event(event)},
                        final=False,
                        include_collector_steps=False,
                    )

        yield self._build_output_from_messages(latest_messages)

    async def astream(self, **kwargs):
        """Stream agent updates asynchronously."""
        logger.debug("Streaming CMSCompOpsAgent asynchronously")
        agent_inputs = self._prepare_agent_inputs(**kwargs)
        latest_messages: List[BaseMessage] = []

        async for event in self.agent.astream(agent_inputs, stream_mode="updates"):
            messages = self._extract_messages(event)
            if messages:
                latest_messages = messages
                content = self._message_content(messages[-1])
                if content:
                    yield self.finalize_output(
                        answer=content,
                        collector=self.active_collector,
                        metadata={"event": self._summarize_event(event)},
                        final=False,
                        include_collector_steps=False,
                    )

        yield self._build_output_from_messages(latest_messages)

    def _update_vector_retriever(self, vectorstore: Any) -> None:
        """Instantiate or refresh the vectorstore retriever tool."""
        search_kwargs = {"k": self.dm_config.get("num_documents_to_retrieve", 3)}

        if self.dm_config.get("use_hybrid_search", False):
            from src.data_manager.vectorstore.retrievers.utils import HybridRetriever

            retriever = HybridRetriever(
                vectorstore=vectorstore,
                search_kwargs=search_kwargs,
                bm25_weight=self.dm_config.get("bm25_weight", 0.6),
                semantic_weight=self.dm_config.get("semantic_weight", 0.4),
                bm25_k1=self.dm_config.get("bm25", {}).get("k1", 0.5),
                bm25_b=self.dm_config.get("bm25", {}).get("b", 0.75),
            )
        else:
            retriever = SemanticRetriever(
                vectorstore=vectorstore,
                search_kwargs=search_kwargs,
                dm_config=self.dm_config,
            )

        self._vector_retriever = retriever
        self._vector_tool = create_retriever_tool(
            retriever,
            name="search_vectorstore",
            description="Query the vectorstore built from local documents.",
            store_docs=self._store_documents,
        )

    def _extract_messages(self, event: Any) -> List[BaseMessage]:
        """Pull LangChain messages from a stream/update payload."""
        if isinstance(event, dict):
            messages = event.get("messages")
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

    def _summarize_event(self, event: Any) -> Dict[str, Any]:
        """Return a lightweight representation of a streaming event."""
        if isinstance(event, dict):
            summary: Dict[str, Any] = {}
            if "node" in event:
                summary["node"] = event["node"]
            if "step" in event:
                summary["step"] = event["step"]
            if "messages" in event:
                summary["messages"] = [self._format_message(msg) for msg in self._extract_messages(event)]
            return summary
        return {"repr": repr(event)}

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
        safe_metadata.setdefault("messages", [self._format_message(msg) for msg in messages])
        return self.finalize_output(
            answer=answer_text,
            collector=self.active_collector,
            metadata=safe_metadata,
            final=final,
        )
