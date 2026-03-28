# Replace LangGraph Agent with GitHub Copilot SDK

## Summary

This PR replaces the LangGraph-based agent runtime with the **GitHub Copilot SDK**, providing a more maintainable and extensible agent pipeline while preserving full backward compatibility with the existing LangGraph pipeline via config-driven selection (`agent_class`).

**47 files changed** | ~6,850 insertions | ~190 deletions

---

## Core Changes

### Copilot SDK Agent Pipeline (`src/archi/pipelines/copilot_agent.py`)
- New `CopilotAgentPipeline` implementing the full agent lifecycle using the Copilot Python SDK
- **BYOK (Bring Your Own Key) provider support** — maps Archi's provider system (OpenAI, Ollama, CERN LiteLLM, etc.) to SDK-compatible parameters
- **Session management** — creates, resumes, and disconnects Copilot agent sessions with proper cleanup
- **Multi-turn history** — prepends prior conversation context for non-resumed sessions so the agent retains memory across turns
- **`/v1` auto-append** — automatically appends `/v1` to base URLs for local providers (Ollama) that require it
- **Customize-mode system messages** — injects system prompt, tools prompt, and role context
- **MCP server passthrough** — forwards user-configured MCP servers to the SDK session

### Copilot Event Adapter (`src/archi/copilot_event_adapter.py`)
- Translates Copilot SDK streaming events (`TextMessageStart`, `ToolCallStart`, `ConfirmationRequest`, etc.) into Archi's `PipelineOutput` dataclass
- Handles tool call lifecycle: name/args accumulation → result capture → UI rendering
- **Session disconnect in `finally` block** — fixes a leak where cleanup only ran on `GeneratorExit`

### Copilot Tool Wrappers (`src/archi/tools/`)
- New `@define_tool` wrappers for all 7 Archi tools:
  - `search_knowledge_base`, `search_local_files`, `search_metadata_index`, `list_metadata_schema`, `fetch_catalog_document`, `monit_opensearch_search`, `monit_opensearch_aggregation`
- `TOOL_REGISTRY` in `__init__.py` provides a central registry of available tools
- `document_collector.py` — shared helper for collecting documents from tool results

### Async Bridge (`src/archi/utils/async_loop.py`)
- `AsyncLoopThread` — runs a persistent asyncio event loop in a background thread, bridging Flask's sync world with the SDK's async API

---

## Security

### Tool Allowlist (Critical Fix)
- **Problem**: The SDK exposes undocumented server-side built-in tools (`sql`, `report_intent`, `bash`, etc.). A blocklist approach only blocked 6 known tools, allowing unknown tools to execute — confirmed in live testing when the model created a SQL database table in response to "remember my name is Jason."
- **Fix**: Replaced `excluded_tools` blocklist with `available_tools` **allowlist** — only the 7 custom Archi tools are permitted. Any new SDK built-in tools are automatically blocked.
- Removed `_COPILOT_BUILTIN_TOOL_BLOCKLIST` constant entirely

---

## Integration Changes

### Chat App Streaming (`src/interfaces/chat_app/app.py`)
- Metadata-based tool call extraction path for Copilot adapter (alongside existing messages-based path for LangGraph)
- `stream_kwargs` pattern replaces direct LLM override for provider/model selection
- Error event handling from agent pipeline
- Tool start trace events emitted for both messages-based and metadata-only paths

### Agent Editor API (`src/interfaces/chat_app/api.py`)
- Agent editor API now returns `{name, description}` objects for tools (was bare strings)
- Only custom Archi tools appear in the editor — SDK built-in tools are excluded

### Pipeline Registry (`src/archi/pipelines/__init__.py`)
- `CopilotAgentPipeline` registered as selectable pipeline class

### Minor Fixes
- Gemini provider removed from provider registry (`src/archi/providers/__init__.py`)
- Default pipeline changed to `CopilotAgentPipeline` in redmine integration
- `config_service.py`: empty string instead of NULL for cleared `active_agent_name`
- `ticket_manager.py`: try/except around collector iteration to prevent crashes
- `local_files.py`: redirect detection for catalog API auth failures
- `mcp_utils.py`: refactored MCP server configuration utilities

---

## Backward Compatibility

The LangGraph pipeline (`BaseReActAgent`, `CMSCompOpsAgent`) remains fully functional. Pipeline selection is config-driven:

```yaml
chatbot:
  agent_class: CopilotAgentPipeline   # or BaseReActAgent
```

Both pipelines pass the same 38-test feature parity matrix.

---

## Test Coverage

| Suite | Count | Status |
|-------|-------|--------|
| Unit tests | 209 | All pass |
| Pipeline matrix (both pipelines) | 38 | All pass |
| Playwright UI tests (submit76) | 392 | All pass |
| Smoke test (submit76 live API) | 1 | Pass |

### New Test Files
- `tests/unit/test_copilot_pipeline.py` — 36 tests for pipeline helpers, tool restrictions, session management
- `tests/unit/test_copilot_event_adapter.py` — adapter event translation tests
- `tests/unit/test_adapter_error_paths.py` — error path coverage
- `tests/unit/test_chat_wrapper_stream.py` — streaming integration tests
- `tests/unit/test_pipeline_integration.py` — pipeline integration tests
- `tests/unit/test_tool_error_handling.py` — tool error handling
- `tests/unit/test_import_sanity.py` — import validation
- `tests/unit/test_ticket_manager.py` — ticket manager error handling
- `tests/test_pipeline_matrix.py` — 38-test feature parity matrix (runs both pipelines)
- `tests/ui/workflows/21-agent-management.spec.ts` — agent editor Playwright tests
- `tests/ui/workflows/22-copilot-streaming.spec.ts` — Copilot streaming Playwright tests
- `tests/smoke/stream_test.py` — streaming smoke test
- `tests/smoke/deploy_preflight.py` — deployment preflight checks

---

## Deployment Notes

- Requires `copilot-sdk` Python package (added to `requirements-base.txt`)
- No database migration needed
- Existing deployments using `BaseReActAgent` require no config changes
- To switch to Copilot pipeline: set `agent_class: CopilotAgentPipeline` in config and provide a GitHub token or compatible BYOK provider credentials
