"""CopilotAgentPipeline — agent pipeline powered by the GitHub Copilot SDK.

Replaces ``BaseReActAgent`` / ``CMSCompOpsAgent`` with a single pipeline
class that creates per-request Copilot SDK sessions.  Streaming events are
translated to ``PipelineOutput`` by :class:`CopilotEventAdapter`.

Design decisions implemented here:
  1  — One CopilotClient at init, per-request sessions, AsyncLoopThread bridge
  1b — invoke()/stream()/astream() signatures match BaseReActAgent
  3  — Event adapter maps SDK events → PipelineOutput
  4  — BYOK-first provider mapping
  8  — MCP config passthrough (archi.mcp_servers → SDK mcpServers)
  13 — Context management delegated to Copilot CLI infinite sessions
  17 — get_tool_registry()/get_tool_descriptions() from TOOL_REGISTRY
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Sequence

from src.archi.copilot_event_adapter import CopilotEventAdapter
from src.archi.utils.async_loop import AsyncLoopThread
from src.archi.utils.output_dataclass import PipelineOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)

# SDK type for Copilot client — resolved at import time if available.
_CopilotClient = None  # type: ignore[assignment]


def _get_copilot_client_cls():
    """Lazy import so units that don't have the SDK installed can still
    import this module for ``get_tool_registry()`` / ``get_tool_descriptions()``."""
    global _CopilotClient
    if _CopilotClient is None:
        from copilot import CopilotClient
        _CopilotClient = CopilotClient
    return _CopilotClient


# ── Provider mapping (decision 4) ────────────────────────────────────────

_PROVIDER_TYPE_MAP = {
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openai",   # OpenRouter is OpenAI-compatible
    "local": "openai",        # Ollama / vLLM expose an OpenAI-compatible API
}


def _build_sdk_provider(
    provider_name: str,
    model_id: str,
    providers_config: dict,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """Translate A2rchi provider config → Copilot SDK ``provider`` dict.

    Parameters
    ----------
    provider_name:
        One of ``"openai"``, ``"anthropic"``, ``"openrouter"``, ``"local"``.
    model_id:
        The model identifier (e.g. ``"gpt-4o"``, ``"claude-sonnet-4-20250514"``).
    providers_config:
        ``services.chat_app.providers`` config section.
    api_key:
        Optional per-user BYOK key.  Falls back to the provider's env var.
    """
    sdk_type = _PROVIDER_TYPE_MAP.get(provider_name.lower())
    if sdk_type is None:
        raise ValueError(
            f"Provider '{provider_name}' cannot be mapped to a Copilot SDK "
            f"BYOK provider.  Supported: {list(_PROVIDER_TYPE_MAP)}."
        )

    provider_cfg = providers_config.get(provider_name.lower(), {})
    result: Dict[str, Any] = {"type": sdk_type}

    base_url = provider_cfg.get("base_url")
    if base_url:
        result["base_url"] = base_url

    if api_key:
        result["api_key"] = api_key
    else:
        # Fallback: let the provider resolve from env
        from src.utils.env import read_secret
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env_var = env_map.get(provider_name.lower())
        if env_var:
            key = read_secret(env_var)
            if key:
                result["api_key"] = key

    return result


# ── History formatter (decision — task 3.4) ──────────────────────────────

def _format_history_as_preamble(history: list) -> str:
    """Convert ``[(sender, content)]`` tuples to a text transcript for the
    system message preamble.

    The Copilot SDK does not accept a list of messages as history.
    Instead, the conversation history is prepended to the system message.
    """
    if not history:
        return ""
    lines: list[str] = ["<conversation_history>"]
    for sender, content in history:
        role = "user" if sender.lower() in ("user", "human") else "assistant"
        lines.append(f"[{role}]: {content}")
    lines.append("</conversation_history>")
    return "\n".join(lines)


# ── MCP config mapping (decision 8 — task 3.5) ──────────────────────────

def _build_mcp_servers(archi_config: dict) -> Optional[dict]:
    """Map ``archi.mcp_servers`` to the SDK's ``mcpServers`` format.

    Existing A2rchi format::

        mcp_servers:
          my_server:
            transport: "stdio"
            command: "uvx"
            args: ["mcp-server-example"]
          web_search:
            transport: "sse"
            url: "http://localhost:8080/sse"

    SDK format::

        mcpServers:
          my_server:
            type: "stdio"
            command: "uvx"
            args: ["mcp-server-example"]
          web_search:
            type: "sse"
            url: "http://localhost:8080/sse"
    """
    raw = archi_config.get("mcp_servers")
    if not raw:
        return None

    result = {}
    for name, cfg in raw.items():
        entry = dict(cfg)
        # Rename 'transport' → 'type' for SDK
        transport = entry.pop("transport", None)
        if transport:
            entry["type"] = transport
        result[name] = entry
    return result or None


# ══════════════════════════════════════════════════════════════════════════
#  CopilotAgentPipeline
# ══════════════════════════════════════════════════════════════════════════

class CopilotAgentPipeline:
    """Agent pipeline backed by the GitHub Copilot SDK.

    The pipeline is instantiated once at startup (via ``archi.update()``).
    Each ``stream()`` / ``invoke()`` call creates a short-lived SDK session
    with the appropriate provider, tools, and system message.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        agent_spec: Optional[Any] = None,
        default_provider: Optional[str] = None,
        default_model: Optional[str] = None,
        **kwargs,
    ) -> None:
        self.config = config
        self.archi_config = config.get("archi") or {}
        self.dm_config = config.get("data_manager", {})

        self.agent_spec = agent_spec
        self.default_provider = default_provider
        self.default_model = default_model

        # Resolve selected tool names from agent spec
        self.selected_tool_names: List[str] = []
        if agent_spec is not None:
            self.selected_tool_names = list(getattr(agent_spec, "tools", []) or [])

        # Read prompt from agent spec or pipeline config
        self.agent_prompt: Optional[str] = None
        if agent_spec is not None:
            self.agent_prompt = getattr(agent_spec, "prompt", None)

        # Providers config (for BYOK mapping)
        services_cfg = config.get("services", {})
        chat_cfg = services_cfg.get("chat_app", {}) if isinstance(services_cfg, dict) else {}
        self._providers_config = chat_cfg.get("providers", {}) if isinstance(chat_cfg, dict) else {}

        # Shared async loop
        self._async_loop = AsyncLoopThread.get_instance()

        # Copilot Client — one per pipeline instance (decision 1)
        self._client = _get_copilot_client_cls()()

        # Optional: catalog client and MONIT client (lazy)
        self._catalog_client = None
        self._monit_client = None
        self._rucio_events_skill = None
        self._init_optional_services()

    def _init_optional_services(self) -> None:
        """Initialise optional service clients (catalog, MONIT)."""
        # Catalog client for file/metadata tools
        try:
            from src.archi.pipelines.agents.tools import RemoteCatalogClient
            self._catalog_client = RemoteCatalogClient.from_deployment_config(self.config)
        except Exception:
            logger.debug("Catalog client not available", exc_info=True)

        # MONIT OpenSearch client
        from src.utils.env import read_secret
        monit_token = read_secret("MONIT_GRAFANA_TOKEN")
        chat_cfg = self.config.get("services", {}).get("chat_app", {})
        monit_url = chat_cfg.get("tools", {}).get("monit", {}).get("url")
        if monit_token and monit_url:
            try:
                from src.archi.pipelines.agents.tools import MONITOpenSearchClient
                self._monit_client = MONITOpenSearchClient(url=monit_url, token=monit_token)
                from src.archi.pipelines.agents.utils.skill_utils import load_skill
                self._rucio_events_skill = load_skill("rucio_events", self.config)
                logger.info("MONIT OpenSearch client initialised")
            except Exception:
                logger.debug("MONIT client init failed", exc_info=True)

    # ── Tool construction ─────────────────────────────────────────────

    def _build_tools(
        self,
        collector,
        vectorstore: Any = None,
    ) -> list:
        """Build the list of ``@define_tool`` functions for a session.

        Only tools listed in ``self.selected_tool_names`` are built.
        If the list is empty all available tools are built.
        """
        from src.archi.tools.file_search import (
            build_document_fetch_tool,
            build_file_search_tool,
            build_metadata_schema_tool,
            build_metadata_search_tool,
        )
        from src.archi.tools.monit_search import (
            build_monit_aggregation_tool,
            build_monit_search_tool,
        )

        store_docs = collector.make_store_docs_callback()
        tools: list = []

        # Normalise legacy tool-name aliases so agent specs written with
        # older names still match the canonical TOOL_REGISTRY entries.
        _ALIASES: Dict[str, str] = {
            "search_vectorstore_hybrid": "search_knowledge_base",
        }
        names: Optional[set] = None
        if self.selected_tool_names:
            names = {_ALIASES.get(n, n) for n in self.selected_tool_names}

        def _want(name: str) -> bool:
            return names is None or name in names

        # Vectorstore retriever tool
        if vectorstore and _want("search_knowledge_base"):
            try:
                from src.archi.tools.retriever import build_retriever_tool
                from src.data_manager.vectorstore.retrievers import HybridRetriever
                retrievers_cfg = self.dm_config.get("retrievers", {})
                hybrid_cfg = retrievers_cfg.get("hybrid_retriever", {})
                k = hybrid_cfg.get("num_documents_to_retrieve", 5)
                bm25_weight = hybrid_cfg.get("bm25_weight", 0.6)
                semantic_weight = hybrid_cfg.get("semantic_weight", 0.4)
                retriever = HybridRetriever(
                    vectorstore=vectorstore,
                    k=k,
                    bm25_weight=bm25_weight,
                    semantic_weight=semantic_weight,
                )
                tools.append(build_retriever_tool(retriever, store_docs=store_docs))
            except Exception:
                logger.warning("Could not build retriever tool", exc_info=True)

        # Catalog tools
        if self._catalog_client:
            if _want("search_local_files"):
                tools.append(build_file_search_tool(
                    self._catalog_client, store_docs=store_docs,
                ))
            if _want("search_metadata_index"):
                tools.append(build_metadata_search_tool(
                    self._catalog_client, store_docs=store_docs,
                ))
            if _want("list_metadata_schema"):
                tools.append(build_metadata_schema_tool(self._catalog_client))
            if _want("fetch_catalog_document"):
                tools.append(build_document_fetch_tool(self._catalog_client))

        # MONIT tools
        if self._monit_client:
            monit_index = "monit_prod_cms_rucio_raw_events*"
            if _want("monit_opensearch_search"):
                tools.append(build_monit_search_tool(
                    self._monit_client,
                    tool_name="rucio_events_search",
                    index=monit_index,
                    skill=self._rucio_events_skill,
                ))
            if _want("monit_opensearch_aggregation"):
                tools.append(build_monit_aggregation_tool(
                    self._monit_client,
                    tool_name="rucio_events_aggregation",
                    index=monit_index,
                    skill=self._rucio_events_skill,
                ))

        return tools

    # ── Session creation ──────────────────────────────────────────────

    def _build_session_config(
        self,
        *,
        history: Optional[list] = None,
        api_key: Optional[str] = None,
        tools: list,
    ) -> dict:
        """Assemble the session config dict for ``client.create_session()``.

        Combines:
          - System message (prompt + history preamble)
          - Provider (BYOK)
          - MCP servers
          - Tools
        """
        # System message = agent prompt + conversation history
        parts: list[str] = []
        if self.agent_prompt:
            parts.append(self.agent_prompt)

        history_text = _format_history_as_preamble(history)
        if history_text:
            parts.append(history_text)

        system_message = "\n\n".join(parts) if parts else None

        cfg: Dict[str, Any] = {}
        if system_message:
            cfg["system_message"] = {"mode": "replace", "content": system_message}

        # Provider (decision 4)
        if self.default_provider and self.default_model:
            cfg["provider"] = _build_sdk_provider(
                self.default_provider,
                self.default_model,
                self._providers_config,
                api_key=api_key,
            )
            cfg["model"] = self.default_model

        # MCP servers (decision 8)
        mcp = _build_mcp_servers(self.archi_config)
        if mcp:
            cfg["mcp_servers"] = mcp

        # Tools are passed to create_session, not in config dict
        cfg["_tools"] = tools

        return cfg

    async def _create_session(
        self,
        adapter: CopilotEventAdapter,
        config: dict,
    ):
        """Create a Copilot SDK session with hooks attached."""
        tools = config.pop("_tools", [])

        from copilot import PermissionHandler

        session = await self._client.create_session(
            tools=tools,
            on_permission_request=PermissionHandler.approve_all,
            streaming=True,
            hooks={
                "on_pre_tool_use": adapter.on_pre_tool_use,
                "on_post_tool_use": adapter.on_post_tool_use,
            },
            **config,
        )
        return session

    # ── Public API ────────────────────────────────────────────────────

    def stream(self, **kwargs) -> Iterator[PipelineOutput]:
        """Stream agent events as ``PipelineOutput`` objects.

        Accepted kwargs: ``history``, ``conversation_id``, ``vectorstore``,
        ``user_id`` (for BYOK resolution).
        """
        history = kwargs.get("history")
        vectorstore = kwargs.get("vectorstore")
        user_id = kwargs.get("user_id")

        # Per-request document collector
        from src.archi.tools import DocumentCollector
        collector = DocumentCollector()

        # Build tools for this request
        tools = self._build_tools(collector, vectorstore=vectorstore)

        # Resolve BYOK key (decision 4)
        api_key = self._resolve_byok_key(user_id)

        # Session config
        session_config = self._build_session_config(
            history=history,
            api_key=api_key,
            tools=tools,
        )

        # Adapter bridges async SDK → sync generator
        adapter = CopilotEventAdapter(self._async_loop)

        # Create session and start consuming events (async)
        async def _run_session():
            try:
                session = await self._create_session(adapter, session_config)

                # Extract last user message from history
                last_msg = ""
                if history:
                    last_pair = history[-1]
                    if last_pair[0].lower() in ("user", "human"):
                        last_msg = last_pair[1]

                # Register event handler and send the user's message
                adapter.attach_to_session(session)
                await session.send_and_wait(last_msg, timeout=120.0)
            except Exception as exc:
                logger.error("Copilot session error: %s", exc, exc_info=True)
                adapter._queue.put(PipelineOutput(
                    answer="",
                    metadata={"event_type": "error", "error": str(exc)},
                    final=False,
                ))
            finally:
                adapter.signal_done()

        # Schedule async work on the background loop
        import concurrent.futures
        future = self._async_loop.run_no_wait(_run_session())

        # Yield events from the sync iterator
        try:
            for output in adapter.iter_outputs():
                yield output
        finally:
            # Wait for async work to finish
            try:
                future.result(timeout=5.0)
            except Exception:
                logger.debug("Session future cleanup error", exc_info=True)

        # Yield the final output with source documents
        final = adapter.build_final_output(
            source_documents=collector.unique_documents(),
            retriever_scores=collector.scores(),
        )
        yield final

    def invoke(self, **kwargs) -> PipelineOutput:
        """Run the agent and return the final ``PipelineOutput``.

        Consumes ``stream()`` internally (decision 1b).
        """
        last_output = None
        for output in self.stream(**kwargs):
            last_output = output
        if last_output is None:
            return PipelineOutput(answer="", final=True)
        return last_output

    async def astream(self, **kwargs) -> AsyncIterator[PipelineOutput]:
        """Async streaming — wraps the sync stream in an executor.

        For true async callers.  The underlying SDK is async but the
        adapter uses a queue bridge, so this is a convenience wrapper.
        """
        import asyncio
        loop = asyncio.get_event_loop()

        q: "asyncio.Queue[Optional[PipelineOutput]]" = asyncio.Queue()

        def _pump():
            try:
                for output in self.stream(**kwargs):
                    loop.call_soon_threadsafe(q.put_nowait, output)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        executor_task = loop.run_in_executor(None, _pump)

        while True:
            item = await q.get()
            if item is None:
                break
            yield item

        await executor_task

    # ── BYOK resolution ──────────────────────────────────────────────

    def _resolve_byok_key(self, user_id: Optional[str]) -> Optional[str]:
        """Resolve a BYOK API key for the current provider and user."""
        if not user_id or not self.default_provider:
            return None
        try:
            from src.archi.providers.byok_resolver import get_byok_resolver
            resolver = get_byok_resolver()
            return resolver.get_byok_key(self.default_provider, user_id)
        except Exception:
            logger.debug("BYOK resolution failed", exc_info=True)
            return None

    # ── Tool registry (decision 17) ──────────────────────────────────

    def get_tool_registry(self) -> Dict[str, Callable]:
        """Return tool name -> factory mapping for the agent spec editor."""
        from src.archi.tools import TOOL_REGISTRY
        return {name: entry["factory"] for name, entry in TOOL_REGISTRY.items()}

    def get_tool_descriptions(self) -> Dict[str, str]:
        """Return tool name -> description mapping for UI display."""
        from src.archi.tools import TOOL_REGISTRY
        return {name: entry["description"] for name, entry in TOOL_REGISTRY.items()}
