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
  SP — Session persistence via resume_session() (stored pipeline_session_id)
  CM — System message customize mode (keep SDK safety defaults)
  EH — onErrorOccurred hook for auto-retry and friendly errors
  ET — Tool lifecycle via streaming events (native toolCallId)
"""

from __future__ import annotations

from typing import (Any, AsyncIterator, Callable, Dict, Iterator, List,
                    Optional, Sequence, Tuple)

from src.archi.pipelines.copilot_agents.copilot_event_adapter import CopilotEventAdapter
from src.archi.utils.async_loop import AsyncLoopThread
from src.archi.utils.output_dataclass import PipelineOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _get_copilot_client_cls():
    """Lazy import so units that don't have the SDK installed can still
    import this module for ``get_tool_registry()`` / ``get_tool_descriptions()``."""
    from copilot import CopilotClient

    return CopilotClient


def _build_tool_restriction_kwargs(custom_tools: list) -> Dict[str, list[str]]:
    """Return Copilot session tool restrictions.

    Uses ``available_tools`` as an **allowlist** containing only the names of
    our custom Archi tools.  This blocks every SDK built-in tool (bash, edit,
    grep, sql, report_intent, etc.) without needing to enumerate them.

    The SDK docs state that ``available_tools`` **takes precedence** over
    ``excluded_tools``.  An empty list means "allow nothing".  When Archi has
    no custom tools, we pass an empty list — which correctly disables all tools.
    """
    allowed = [t.name for t in custom_tools]
    return {
        "available_tools": allowed,
    }


# ── Provider mapping (decision 4) ────────────────────────────────────────

_PROVIDER_TYPE_MAP = {
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openai",  # OpenRouter is OpenAI-compatible
    "local": "openai",  # Ollama / vLLM expose an OpenAI-compatible API
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
    if not base_url:
        # Default base URLs for known OpenAI-compatible providers
        _DEFAULT_BASE_URLS = {
            "openrouter": "https://openrouter.ai/api/v1",
        }
        base_url = _DEFAULT_BASE_URLS.get(provider_name.lower())
    if base_url:
        # The Copilot SDK uses OpenAI-compatible endpoints (/chat/completions)
        # directly under base_url.  Ollama (and similar local servers) serve
        # that API under /v1, so append it when missing.
        if provider_name.lower() == "local" and not base_url.rstrip("/").endswith(
            "/v1"
        ):
            base_url = base_url.rstrip("/") + "/v1"
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
        chat_cfg = (
            services_cfg.get("chat_app", {}) if isinstance(services_cfg, dict) else {}
        )
        self._providers_config = (
            chat_cfg.get("providers", {}) if isinstance(chat_cfg, dict) else {}
        )

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

            self._catalog_client = RemoteCatalogClient.from_deployment_config(
                self.config
            )
        except Exception:
            logger.debug("Catalog client not available", exc_info=True)

        # MONIT OpenSearch client
        from src.utils.env import read_secret

        monit_token = read_secret("MONIT_GRAFANA_TOKEN")
        chat_cfg = self.config.get("services", {}).get("chat_app", {})
        monit_url = chat_cfg.get("tools", {}).get("monit", {}).get("url")
        if monit_token and monit_url:
            try:
                from src.archi.pipelines.agents.tools import \
                    MONITOpenSearchClient

                self._monit_client = MONITOpenSearchClient(
                    url=monit_url, token=monit_token
                )
                from src.archi.pipelines.agents.utils.skill_utils import \
                    load_skill

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
        from src.archi.pipelines.copilot_agents.tools.file_search import (
            build_document_fetch_tool, build_file_search_tool,
            build_metadata_schema_tool, build_metadata_search_tool)
        from src.archi.pipelines.copilot_agents.tools.monit_search import (
            build_monit_aggregation_tool, build_monit_search_tool)

        store_docs = collector.make_store_docs_callback()
        tools: list = []

        names: Optional[set] = None
        if self.selected_tool_names:
            names = set(self.selected_tool_names)

        def _want(name: str) -> bool:
            return names is None or name in names

        # Vectorstore retriever tool
        if vectorstore and _want("search_knowledge_base"):
            try:
                from src.archi.pipelines.copilot_agents.tools.retriever import build_retriever_tool
                from src.data_manager.vectorstore.retrievers import \
                    HybridRetriever

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
                tools.append(
                    build_file_search_tool(
                        self._catalog_client,
                        store_docs=store_docs,
                    )
                )
            if _want("search_metadata_index"):
                tools.append(
                    build_metadata_search_tool(
                        self._catalog_client,
                        store_docs=store_docs,
                    )
                )
            if _want("list_metadata_schema"):
                tools.append(build_metadata_schema_tool(self._catalog_client))
            if _want("fetch_catalog_document"):
                tools.append(build_document_fetch_tool(self._catalog_client))

        # MONIT tools
        if self._monit_client:
            monit_index = "monit_prod_cms_rucio_raw_events*"
            if _want("monit_opensearch_search"):
                tools.append(
                    build_monit_search_tool(
                        self._monit_client,
                        index=monit_index,
                        skill=self._rucio_events_skill,
                    )
                )
            if _want("monit_opensearch_aggregation"):
                tools.append(
                    build_monit_aggregation_tool(
                        self._monit_client,
                        index=monit_index,
                        skill=self._rucio_events_skill,
                    )
                )

        return tools

    # ── Session creation ──────────────────────────────────────────────

    def _build_session_config(
        self,
        *,
        api_key: Optional[str] = None,
        tools: list,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> dict:
        """Assemble the session config dict for ``client.create_session()``.

        Combines:
          - System message (customize mode — keep SDK defaults)
          - Provider (BYOK)
          - MCP servers
          - Tools
        """
        cfg: Dict[str, Any] = {}

        # System message (customize mode — decision CM)
        if self.agent_prompt:
            cfg["system_message"] = {
                "mode": "customize",
                "sections": {
                    "identity": {
                        "action": "replace",
                        "content": self.agent_prompt,
                    },
                },
            }

        # Provider (decision 4) — per-request overrides take precedence
        effective_provider = provider_override or self.default_provider
        effective_model = model_override or self.default_model
        if effective_provider and effective_model:
            cfg["provider"] = _build_sdk_provider(
                effective_provider,
                effective_model,
                self._providers_config,
                api_key=api_key,
            )
            cfg["model"] = effective_model

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
        *,
        session_id: Optional[str] = None,
    ) -> Tuple[Any, bool]:
        """Create or resume a Copilot SDK session with hooks attached.

        Returns
        -------
        (session, was_resumed) : tuple
            The SDK session and whether it was resumed from a prior session_id.
        """
        tools = config.pop("_tools", [])

        hooks = {
            "on_error_occurred": self._on_error_occurred,
        }
        tool_restrictions = _build_tool_restriction_kwargs(tools)

        if session_id:
            # Resume existing session — SDK manages conversation history
            try:
                session = await self._client.resume_session(
                    session_id,
                    tools=tools,
                    on_permission_request=self._on_permission_request,
                    streaming=True,
                    hooks=hooks,
                    **tool_restrictions,
                    **config,
                )
                logger.debug("Resumed session %s", session_id)
                return session, True
            except Exception:
                logger.info(
                    "Could not resume session %s — creating new",
                    session_id,
                    exc_info=True,
                )
                # Don't reuse a failed session_id for the new session
                session_id = None

        # Create a new session
        create_kwargs: Dict[str, Any] = dict(
            tools=tools,
            on_permission_request=self._on_permission_request,
            streaming=True,
            hooks=hooks,
            **tool_restrictions,
            **config,
        )
        if session_id:
            create_kwargs["session_id"] = session_id

        # Log provider type/model without leaking API keys
        provider_info = config.get("provider", "default")
        if isinstance(provider_info, dict):
            provider_info = {k: v for k, v in provider_info.items() if k != "api_key"}
        logger.info(
            "Creating Copilot session with %d tools, restrictions=%s, provider=%s, model=%s",
            len(tools),
            tool_restrictions,
            provider_info,
            config.get("model", "default"),
        )

        session = await self._client.create_session(**create_kwargs)
        return session, False

    # ── Error hook (decision EH) ──────────────────────────────────────

    def _on_error_occurred(self, hook_input, context=None):
        """Handle SDK errors — retry transient model errors, log all."""
        error = (
            hook_input.get("error", "")
            if isinstance(hook_input, dict)
            else getattr(hook_input, "error", "")
        )
        error_context = (
            hook_input.get("errorContext", "")
            if isinstance(hook_input, dict)
            else getattr(hook_input, "errorContext", "")
        )
        recoverable = (
            hook_input.get("recoverable", False)
            if isinstance(hook_input, dict)
            else getattr(hook_input, "recoverable", False)
        )

        logger.error(
            "Copilot SDK error: context=%s recoverable=%s error=%s",
            error_context,
            recoverable,
            error,
        )

        if recoverable and error_context == "model_call":
            return {
                "errorHandling": "retry",
                "retryCount": 2,
                "userNotification": "Model request failed, retrying...",
            }

        # Non-recoverable: let SDK handle it (session.error event will fire)
        return None

    def _allowed_custom_tool_names(self) -> set[str]:
        """Return the set of Archi custom tools the active agent is allowed to run."""
        from src.archi.pipelines.copilot_agents.tools import TOOL_REGISTRY

        if not self.selected_tool_names:
            return set(TOOL_REGISTRY.keys())
        return set(self.selected_tool_names)

    def _on_permission_request(self, request, invocation):
        """Allow only declared Archi custom tools and deny SDK built-ins."""
        from copilot.generated.session_events import PermissionRequestKind
        from copilot.types import PermissionRequestResult

        kind = getattr(request, "kind", None)
        tool_name = getattr(request, "tool_name", "") or ""
        command_text = getattr(request, "full_command_text", None)

        if kind == PermissionRequestKind.CUSTOM_TOOL:
            if tool_name in self._allowed_custom_tool_names():
                return PermissionRequestResult(kind="approved")
            logger.warning(
                "Denied custom tool permission request: tool=%s invocation=%s",
                tool_name or "<missing>",
                invocation,
            )
            return PermissionRequestResult(
                kind="denied",
                message=f"Tool '{tool_name or 'unknown'}' is not allowed in this Archi agent.",
            )

        logger.warning(
            "Denied non-custom permission request: kind=%s tool=%s command=%s invocation=%s",
            getattr(kind, "value", kind),
            tool_name or "<none>",
            command_text or "<none>",
            invocation,
        )
        return PermissionRequestResult(
            kind="denied",
            message="Only Archi custom tools are allowed in this deployment.",
        )

    # ── Public API ────────────────────────────────────────────────────

    def stream(self, **kwargs) -> Iterator[PipelineOutput]:
        """Stream agent events as ``PipelineOutput`` objects.

        Accepted kwargs: ``history``, ``conversation_id``,
        ``pipeline_session_id``, ``vectorstore``, ``user_id`` (for BYOK
        resolution), ``provider``, ``model``, ``provider_api_key``
        (per-request overrides from settings UI).
        """
        history = kwargs.get("history")
        conversation_id = kwargs.get("conversation_id")
        vectorstore = kwargs.get("vectorstore")
        user_id = kwargs.get("user_id")
        provider_override = kwargs.get("provider")
        model_override = kwargs.get("model")
        session_api_key = kwargs.get("provider_api_key")

        # Per-request document collector
        from src.archi.pipelines.copilot_agents.tools import DocumentCollector

        collector = DocumentCollector()

        # Build tools for this request
        tools = self._build_tools(collector, vectorstore=vectorstore)
        logger.info(
            "Built %d tools for session: %s",
            len(tools),
            [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools],
        )

        # Resolve BYOK key: session-provided key takes precedence over DB key
        api_key = session_api_key or self._resolve_byok_key(user_id)

        # Session config
        session_config = self._build_session_config(
            api_key=api_key,
            tools=tools,
            provider_override=provider_override,
            model_override=model_override,
        )

        # Resume only when chat metadata has a real Copilot SDK session ID.
        session_id = kwargs.get("pipeline_session_id")

        # Adapter bridges async SDK → sync generator
        adapter = CopilotEventAdapter(self._async_loop)
        active_session_id: Optional[str] = None

        # Create session and start consuming events (async)
        async def _run_session():
            nonlocal active_session_id
            try:
                session, was_resumed = await self._create_session(
                    adapter,
                    session_config,
                    session_id=session_id,
                )
                active_session_id = getattr(session, "session_id", None)

                # Build the prompt.  The SDK session is stateful so when
                # resumed it already knows prior turns.  For a *new* session
                # with prior history we prepend earlier turns so the model
                # has full context.
                last_msg = ""
                if history:
                    last_pair = history[-1]
                    if last_pair[0].lower() in ("user", "human"):
                        last_msg = last_pair[1]

                    # Prepend earlier turns when there are >1 history pairs
                    # and the session was freshly created (not resumed).
                    if len(history) > 1 and not was_resumed:
                        prior = []
                        for role, content in history[:-1]:
                            label = (
                                "User"
                                if role.lower() in ("user", "human")
                                else "Assistant"
                            )
                            prior.append(f"{label}: {content}")
                        prefix = "\n".join(prior)
                        last_msg = (
                            f"[Prior conversation context]\n{prefix}\n"
                            f"[End of prior context]\n\n{last_msg}"
                        )

                # Register event handler and send the user's message
                adapter.attach_to_session(session)
                await session.send_and_wait(last_msg, timeout=120.0)
            except Exception as exc:
                logger.error("Copilot session error: %s", exc, exc_info=True)
                adapter._queue.put(
                    PipelineOutput(
                        answer="",
                        metadata={"event_type": "error", "error": str(exc)},
                        final=False,
                    )
                )
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
        if active_session_id:
            final.metadata["pipeline_session_id"] = active_session_id
        yield final

    def supports_persisted_session_id(self) -> bool:
        """Copilot sessions can be resumed using a persisted SDK session ID."""
        return True

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
        from src.archi.pipelines.copilot_agents.tools import TOOL_REGISTRY

        return {name: entry["factory"] for name, entry in TOOL_REGISTRY.items()}

    def get_tool_descriptions(self) -> Dict[str, str]:
        """Return tool name -> description mapping for UI display."""
        from src.archi.pipelines.copilot_agents.tools import TOOL_REGISTRY

        return {name: entry["description"] for name, entry in TOOL_REGISTRY.items()}
