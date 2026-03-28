# Multi-Backend Agent Abstraction: Recommendation

**Date:** March 25, 2026
**Question:** Should A2rchi support a general agent backend (Copilot SDK, Claude Agent SDK, LangChain) or lock into the Copilot SDK?

**Verdict: Lock into Copilot SDK. A general abstraction is feasible but not advisable.**

## Side-by-Side Comparison

| Dimension | Copilot SDK | Claude Agent SDK | LangChain |
|---|---|---|---|
| **Runtime** | CLI subprocess (`copilot --headless`) | CLI subprocess (`claude` CLI) | In-process graph |
| **Tool definition** | `defineTool(name, {description, parameters: JSONSchema, handler})` | `@tool(name, desc, schema)` → must return MCP `{"content": [...]}` | `@tool` decorator, returns `str` |
| **Streaming** | Event callbacks: `session.on("event_type", handler)` | Async iterator: `async for msg in query()` | State generator: `for chunk in agent.stream()` |
| **Session** | `createSession()` → `sendAndWait()`, managed by CLI | `query()` (stateless) or `ClaudeSDKClient` (sessioned), managed by CLI | `invoke(state)`, state is external (you manage it) |
| **Models** | GPT-4.1 default, BYOK for OpenAI/Azure/Anthropic/Google/Mistral | Claude only, BYOK via Bedrock/Vertex/Azure AI Foundry | Any provider via `init_chat_model()` |
| **Hooks** | `onPreToolUse`, `onPostToolUse`, session lifecycle | `PreToolUse`, `PostToolUse`, `PermissionRequest`, etc. | Middleware: `@before_model`, `@after_model`, `@wrap_tool_call` |
| **Auth** | GitHub OAuth, env vars, BYOK | Anthropic API key, Bedrock, Vertex | Per-model provider keys |

## Key Issues With a General Abstraction

### 1. Tool return format mismatch

Claude Agent SDK enforces MCP wire format — tools must return `{"content": [{"type": "text", "text": "..."}]}`. Copilot tools return any serializable value. LangChain tools return strings. Every tool needs a per-backend wrapper that normalizes both input schemas and return formats. Our 7 tools become 21 adapter functions.

### 2. Both Copilot and Claude SDKs are CLI wrappers

They spawn a subprocess and communicate over stdio/TCP. LangChain runs fully in-process. This means:

- Two separate CLI binaries in your Docker image
- Two different auth flows (GitHub OAuth vs Anthropic API key)
- Two different process lifecycle managers
- LangChain requires none of this (but has completely different plumbing)

### 3. Three incompatible streaming models

Our existing `copilot_event_adapter.py` is ~400 lines that translate Copilot's event callbacks into `PipelineOutput` objects. We'd need an equivalent adapter for each backend — each handling different event types, different data shapes, different async patterns (callbacks vs async iterators vs sync generators).

### 4. Claude Agent SDK BYOK is provider-level, not model-level

The Claude Agent SDK does support BYOK via Amazon Bedrock, Google Vertex AI, and Microsoft Azure AI Foundry. But this means "bring your own cloud credentials to access **Claude models**" — not "bring your own key to use any model." You're still restricted to Claude (Sonnet, Opus, Haiku). Copilot SDK's BYOK lets you swap between entirely different model families (GPT-4.1, Claude, Gemini, Mistral). A2rchi's multi-provider model selection would not work through the Claude Agent SDK.

### 5. Session lifecycle is fundamentally different

Copilot and Claude manage sessions inside their CLI process (persist, resume, fork). LangChain has no built-in session — you provide state via checkpointers. Abstracting over "session" means accepting the lowest common denominator: no resume, no persistence, no fork.

### 6. LCD strips unique value from each SDK

- **Copilot:** Custom agents, skills, system message section overrides (replace/remove/append per section) — can't express through an abstraction
- **Claude:** Permission system, sandbox, file checkpointing, subagents — not available in others
- **LangChain:** Middleware pipeline, dynamic model selection, structured output strategies — completely different paradigm

## The Math

Each additional backend requires:

| Component | LOC |
|---|---|
| Event/streaming adapter | ~400 |
| Tool wrappers (7 tools × format normalization) | ~200 |
| Session lifecycle management | ~300 |
| Auth/config integration | ~150 |
| **Total per backend** | **~1,050** |

Plus ongoing maintenance when any SDK ships breaking changes.

## Why the Architecture Already Supports a Future Pivot

The current architecture is already well-separated:

- **`archi.py`** is 100% backend-agnostic — it calls `pipeline.stream()` and validates `PipelineOutput`
- The pipeline factory (`getattr(archiPipelines, class_name)`) lets you add a `LangChainAgentPipeline` or `ClaudeAgentPipeline` as a new pipeline class without touching any shared code
- **`PipelineOutput`** is the universal contract — any new backend just needs to yield these

No premature abstraction layer needed. When the time comes, you add a new pipeline class.

## If You Ever Need a Second Backend

**LangChain is the better addition** (not Claude Agent SDK) because:

1. It runs in-process (no CLI dependency)
2. It supports any model provider
3. Its `@tool` decorator is closest to Copilot's `defineTool`

But even then, it's ~1,000+ LOC of glue code for marginal value — the same users who want "Anthropic models" already get them through Copilot SDK's BYOK.

## Recommendation

Stay on the Copilot SDK. Build the second backend only when a concrete use case demands it — the architecture is ready.
