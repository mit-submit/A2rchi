# Replace LangGraph Agent with GitHub Copilot SDK

## Summary

This PR replaces the LangGraph-based agent runtime with the **GitHub Copilot SDK**, providing a more maintainable and extensible agent pipeline while preserving full backward compatibility with the existing LangGraph pipeline via config-driven selection (`agent_class`).

**123 files changed** | ~18,800 insertions | ~4,500 deletions | 114 non-merge commits

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

### RBAC System (`src/utils/rbac/`)
- New role-based access control system with JWT parsing, permission enums, decorators, audit logging, and a permission registry
- Integrated across chat app, data viewer, and agent editor endpoints

---

## Additional Features

### CERN LiteLLM Provider (`src/archi/providers/cern_litellm_provider.py`)
- New provider class for CERN's LiteLLM proxy service

### Service Status Board (`src/interfaces/chat_app/service_alerts.py`)
- Alert management with REST API endpoints
- Alert banner template integration in chat UI

### Agent Editor Improvements
- Agent editor API now returns `{name, description}` objects for tools (was bare strings)
- Only custom Archi tools appear in the editor — SDK built-in tools are excluded

### Provider Management
- Enable/disable providers via config
- Per-message model label tracking and display
- Provider override support with `model_used` field in conversation history

### Data Manager
- Scheduler improvements (runs even with no schedules, updates vectorstore after jobs)
- SSO scraper improvements with remote Selenium driver support
- Catalog postgres enhancements
- Ticket manager refactoring

### UI Improvements
- Context meter with hover explanation
- Collapsible sources display
- Upload UI improvements
- Status page (`status.html`)
- Tool name/args now display correctly in streaming UI (was showing "unknown")

### Timestamps
- All `TIMESTAMP` columns migrated to `TIMESTAMPTZ`
- `utcnow()` replaced with timezone-aware `now()`
- API responses include Z suffix for ISO timestamps

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
| Unit tests | 209 | ✅ All pass |
| Pipeline matrix (both pipelines) | 38 | ✅ All pass |
| Playwright UI tests | 398 | ✅ All pass |
| Live E2E browser tests (submit76) | 8 | ✅ All pass |

### New Test Files
- `tests/unit/test_copilot_pipeline.py` — 36 tests for pipeline helpers, tool restrictions, session management
- `tests/unit/test_copilot_event_adapter.py` — adapter event translation tests
- `tests/unit/test_adapter_error_paths.py` — error path coverage
- `tests/unit/test_chat_wrapper_stream.py` — streaming integration tests
- `tests/unit/test_pipeline_integration.py` — pipeline integration tests
- `tests/unit/test_tool_error_handling.py` — tool error handling
- `tests/unit/test_import_sanity.py` — import validation
- `tests/test_pipeline_matrix.py` — 38-test feature parity matrix (runs both pipelines)
- `tests/ui/workflows/21-agent-management.spec.ts` — agent editor Playwright tests
- `tests/ui/workflows/22-copilot-streaming.spec.ts` — Copilot streaming Playwright tests
- `tests/smoke/stream_test.py` — streaming smoke test
- `tests/smoke/deploy_preflight.py` — deployment preflight checks

---

## Key Commits

| Commit | Description |
|--------|-------------|
| `0e33105d` | Replace agent runtime with Copilot SDK |
| `e546f491` | Fix critical bugs in Copilot SDK integration |
| `4ab1256b` | Fix tool restrictions, session resume, event adapter edge cases |
| `9efb9b53` | Switch SDK tool restrictions to allowlist |
| `64a61ab2` | Security: tool allowlist, multi-turn history fix, session leak fix |
| `5b3a079e` | Implement RBAC system |
| `98cfb844` | Add service alert management |
| `f643cfdf` | Migrate TIMESTAMP to TIMESTAMPTZ |

---

## Deployment Notes

- Requires `copilot-sdk` Python package (added to `requirements-base.txt`)
- No database migration script — new columns/tables are created via `init.sql` for fresh deployments
- Existing deployments using `BaseReActAgent` require no config changes
- To switch to Copilot pipeline: set `agent_class: CopilotAgentPipeline` in config and provide a GitHub token or compatible BYOK provider credentials
