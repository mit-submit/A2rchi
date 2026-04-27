"""
OpenAI-compatible API blueprint for archi.

Provides /v1/models and /v1/chat/completions endpoints that allow
OpenAI-compatible clients (Open WebUI, LiteLLM, Continue.dev, etc.)
to use archi as a backend.

Registered conditionally via services.chat_app.openai_compat.enabled config.
"""

import json
import re
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from types import SimpleNamespace
from typing import Any

import psycopg2
from flask import Blueprint, Response, jsonify, request, g

from src.archi.utils.citation_formatter import format_citations
from src.utils.logging import get_logger
from src.utils.rbac import Permission, has_permission, get_registry
from src.utils.rbac.audit import log_authentication_event, log_permission_check

logger = get_logger(__name__)

# Strip <think>...</think> blocks from assistant messages echoed back by
# Open WebUI on subsequent turns — otherwise Archi's model sees its own
# previous tool dumps as conversation context.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

openai_compat = Blueprint("openai_compat", __name__, url_prefix="/v1")

# Module-level references, set during registration
_chat_wrapper: Any = None
_auth_enabled: bool = False
_boot_timestamp = int(time.time())

# Open WebUI runs in host netns on the same machine as Archi; the host
# firewall already drops everything except loopback. /v1 trusts the
# X-OpenWebUI-User-* headers when the call comes from these addresses.
_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def register_openai_compat(app, chat_wrapper, *, auth_enabled=False):
    """
    Register the OpenAI-compatible blueprint with a Flask app.

    Args:
        app: The Flask application
        chat_wrapper: The ChatWrapper instance for pipeline access
        auth_enabled: Whether authentication is enabled
    """
    global _chat_wrapper, _auth_enabled
    _chat_wrapper = chat_wrapper
    _auth_enabled = auth_enabled
    app.register_blueprint(openai_compat)
    logger.info("Registered OpenAI-compatible API blueprint at /v1")


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def _openai_error(message, error_type="invalid_request_error", status=400):
    """Return an OpenAI-format error response."""
    return jsonify({"error": {"message": message, "type": error_type}}), status


def require_owui_auth(f):
    """Decorator for /v1 routes.

    Trusts the X-OpenWebUI-User-* headers when the request comes from
    loopback. The OWUI role header ("admin"/"user"/"pending") is mapped
    onto Archi RBAC roles: admin → archi-admins, anything else → the
    registry's default role.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _auth_enabled:
            return f(*args, **kwargs)

        registry = get_registry()
        peer = request.remote_addr or ""
        email = (request.headers.get("X-OpenWebUI-User-Email") or "").strip()
        endpoint = request.endpoint or "/v1"

        if peer not in _LOOPBACK_ADDRS:
            log_authentication_event(
                "unknown", "owui_header_auth", success=False,
                method="owui_headers", details=f"non-loopback peer {peer!r}"
            )
            return _openai_error("Authentication required", status=401)

        if not email:
            if registry.allow_anonymous:
                roles = [registry.default_role]
                granted = has_permission(Permission.Chat.QUERY, roles)
                log_authentication_event("anonymous", "api_anonymous_access", success=True, method="anonymous")
                log_permission_check("anonymous", str(Permission.Chat.QUERY), granted=granted, endpoint=endpoint, roles=roles)
                if not granted:
                    return _openai_error("Permission denied", status=403)
                g.v1_user = None
                g.v1_roles = roles
                return f(*args, **kwargs)
            log_authentication_event(
                "unknown", "owui_header_auth", success=False,
                method="owui_headers", details="missing X-OpenWebUI-User-Email"
            )
            return _openai_error("Authentication required", status=401)

        owui_role = (request.headers.get("X-OpenWebUI-User-Role") or "").lower().strip()
        if owui_role == "admin":
            roles = ["archi-admins"]
        else:
            roles = [registry.default_role]

        granted = has_permission(Permission.Chat.QUERY, roles)
        log_permission_check(email, str(Permission.Chat.QUERY), granted=granted, endpoint=endpoint, roles=roles)
        if not granted:
            log_authentication_event(email, "owui_header_auth", success=False, method="owui_headers", details="permission denied")
            return _openai_error("Permission denied", status=403)

        log_authentication_event(email, "owui_header_auth", success=True, method="owui_headers")
        g.v1_user = SimpleNamespace(id=email, email=email)
        g.v1_roles = roles
        return f(*args, **kwargs)

    return decorated


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------

@openai_compat.route("/models", methods=["GET"])
@require_owui_auth
def list_models():
    """Return available archi configs as OpenAI model objects."""
    from src.utils.config_access import get_full_config

    config = get_full_config()
    config_name = config.get("name", "default")

    models = [
        {
            "id": config_name,
            "object": "model",
            "created": _boot_timestamp,
            "owned_by": "archi",
        }
    ]

    return jsonify({"object": "list", "data": models})


# ---------------------------------------------------------------------------
# POST /v1/chat/completions
# ---------------------------------------------------------------------------

@openai_compat.route("/chat/completions", methods=["POST"])
@require_owui_auth
def chat_completions():
    """Handle OpenAI-compatible chat completion requests."""
    data = request.get_json(silent=True)
    if not data:
        return _openai_error("Request body must be valid JSON")

    model = data.get("model")
    messages = data.get("messages")
    if not model:
        return _openai_error("'model' is required")
    if not messages or not isinstance(messages, list):
        return _openai_error("'messages' must be a non-empty array")

    from src.utils.config_access import get_full_config
    config = get_full_config()
    available_config = config.get("name", "default")
    if model != available_config:
        return _openai_error(
            f"Model '{model}' not found. Available: {available_config}",
            "invalid_request_error",
            404,
        )

    query = messages[-1].get("content", "")
    history = _messages_to_history(messages[:-1])
    last_message = [("user", query)]

    stream = data.get("stream", False)

    user_id = None
    if hasattr(g, "v1_user") and g.v1_user:
        user_id = g.v1_user.id

    # Stable client_id for the lifetime of this request — used for both
    # conversation creation and the stream call so DB access checks pass.
    client_id = user_id or f"v1_{uuid.uuid4().hex[:12]}"

    external_chat_id = request.headers.get("X-OpenWebUI-Chat-Id")
    conversation_id = None
    if external_chat_id and _chat_wrapper:
        conversation_id = _get_or_create_conversation(
            external_chat_id, user_id, client_id
        )

    now = datetime.now(timezone.utc)
    stream_kwargs = {
        "message": last_message,
        "conversation_id": conversation_id,
        "client_id": client_id,
        "is_refresh": False,
        "server_received_msg_ts": now,
        "client_sent_msg_ts": now.timestamp(),
        "client_timeout": 600.0,
        "config_name": model,
        "include_agent_steps": True,
        "include_tool_steps": True,
        "user_id": user_id,
        "external_history": history,
    }

    request_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if stream:
        return _streaming_response(request_id, model, stream_kwargs)
    else:
        return _non_streaming_response(request_id, model, stream_kwargs)


# ---------------------------------------------------------------------------
# Chunk accumulation helper
# ---------------------------------------------------------------------------

def _extract_delta(content, accumulated):
    """Extract the new delta from a chunk that may be cumulative or incremental.

    Returns (delta, new_accumulated). delta is None if the chunk is a duplicate.
    """
    if content.startswith(accumulated) and len(content) > len(accumulated):
        return content[len(accumulated):], content
    if accumulated.endswith(content):
        return None, accumulated
    return content, accumulated + content


# ---------------------------------------------------------------------------
# Streaming response
# ---------------------------------------------------------------------------

def _streaming_response(request_id, model, stream_kwargs):
    """Return an SSE streaming response."""

    def generate():
        accumulated = [""]
        source_documents = []
        source_scores = []
        # Open WebUI renders <think>...</think> as a collapsible "Thought"
        # block. We open it on the first tool/thinking event and close it
        # before the actual answer text starts streaming.
        trace = {"opened": False, "closed": False}

        def open_trace():
            if not trace["opened"]:
                trace["opened"] = True
                return _sse_chunk(request_id, model, content="<think>\n")
            return None

        def close_trace():
            if trace["opened"] and not trace["closed"]:
                trace["closed"] = True
                return _sse_chunk(request_id, model, content="\n</think>\n\n")
            return None

        try:
            for event in _chat_wrapper.stream(**stream_kwargs):
                event_type = event.get("type", "")

                if event_type in ("tool_start", "thinking_start"):
                    opener = open_trace()
                    if opener:
                        yield opener
                    if event_type == "tool_start":
                        name = event.get("tool_name", "unknown")
                        yield _sse_chunk(request_id, model, content=f"\n- **{name}**")

                elif event_type == "tool_end":
                    duration = event.get("duration_ms")
                    if duration is not None:
                        yield _sse_chunk(request_id, model, content=f" ({duration} ms)")

                elif event_type == "thinking_end":
                    text = (event.get("thinking_content") or "")[:200]
                    if text:
                        yield _sse_chunk(request_id, model, content=f"\n  _{text}_")

                elif event_type == "chunk":
                    content = event.get("content", "")
                    if content:
                        closer = close_trace()
                        if closer:
                            yield closer
                        delta, accumulated[0] = _extract_delta(content, accumulated[0])
                        if delta:
                            yield _sse_chunk(request_id, model, content=delta)

                elif event_type == "final":
                    closer = close_trace()
                    if closer:
                        yield closer
                    docs = event.get("source_documents", [])
                    scores_list = event.get("retriever_scores", [])
                    if docs:
                        source_documents = docs
                        source_scores = scores_list

                    citation_text = format_citations(source_documents, source_scores)
                    if citation_text:
                        accumulated[0] += citation_text
                        yield _sse_chunk(request_id, model, content=citation_text)

                    yield _sse_chunk(request_id, model, finish_reason="stop")
                    yield "data: [DONE]\n\n"

                    return

                elif event_type == "error":
                    closer = close_trace()
                    if closer:
                        yield closer
                    error_msg = event.get("message", "Unknown error")
                    yield _sse_chunk(request_id, model, content=f"\n\n[Error: {error_msg}]")
                    yield _sse_chunk(request_id, model, finish_reason="stop")
                    yield "data: [DONE]\n\n"

                    return

            # No final event received — close the stream
            closer = close_trace()
            if closer:
                yield closer
            yield _sse_chunk(request_id, model, finish_reason="stop")
            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.error(f"/v1 streaming error: {exc}", exc_info=True)
            closer = close_trace()
            if closer:
                yield closer
            yield _sse_chunk(request_id, model, content="\n\n[Error: server error; see chat logs for message]")
            yield _sse_chunk(request_id, model, finish_reason="stop")
            yield "data: [DONE]\n\n"

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Non-streaming response
# ---------------------------------------------------------------------------

def _non_streaming_response(request_id, model, stream_kwargs):
    """Accumulate from stream() and return a complete JSON response."""
    final_content = ""
    source_documents = []
    source_scores = []
    trace_lines: list[str] = []

    try:
        for event in _chat_wrapper.stream(**stream_kwargs):
            event_type = event.get("type", "")

            if event_type == "tool_start":
                trace_lines.append(f"\n- **{event.get('tool_name', 'unknown')}**")

            elif event_type == "tool_end":
                duration = event.get("duration_ms")
                if duration is not None and trace_lines:
                    trace_lines[-1] += f" ({duration} ms)"

            elif event_type == "thinking_end":
                text = (event.get("thinking_content") or "")[:200]
                if text:
                    trace_lines.append(f"\n  _{text}_")

            elif event_type == "final":
                # The final event's response is a plain string from
                # ChatWrapper._finalize_result — use it directly.
                response = event.get("response")
                if response:
                    final_content = response or ""
                docs = event.get("source_documents", [])
                scores_list = event.get("retriever_scores", [])
                if docs:
                    source_documents = docs
                    source_scores = scores_list

            elif event_type == "error":
                error_msg = event.get("message", "Unknown error")
                return _openai_error(error_msg, "server_error", 500)

    except Exception as exc:
        logger.error(f"/v1 non-streaming error: {exc}", exc_info=True)
        return _openai_error("server error; see chat logs for message", "server_error", 500)

    citation_text = format_citations(source_documents, source_scores)
    if citation_text:
        final_content += citation_text

    if trace_lines:
        final_content = "<think>\n" + "".join(trace_lines) + "\n</think>\n\n" + final_content

    return jsonify({
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": final_content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    })


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse_chunk(request_id, model, content=None, finish_reason=None):
    """Build a single SSE data line in OpenAI format."""
    delta = {}
    if content is not None:
        delta["content"] = content
    if finish_reason is not None:
        delta["role"] = "assistant"

    chunk = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _messages_to_history(messages):
    """Convert OpenAI messages array to archi history tuples.

    Returns a list of (sender, content) tuples compatible with
    _prepare_chat_context / _prepare_inputs.  System messages are
    stripped — archi uses its own system prompt from the agent spec.
    """
    history = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            continue
        elif role == "assistant":
            content = _THINK_BLOCK_RE.sub("", content)
            history.append(("archi", content))
        else:
            history.append(("user", content))
    return history


# ---------------------------------------------------------------------------
# Conversation persistence
# ---------------------------------------------------------------------------

def _get_or_create_conversation(external_chat_id, user_id, client_id):
    """Look up or create an archi conversation for an external chat ID."""
    if not _chat_wrapper:
        return None

    try:
        conn = psycopg2.connect(**_chat_wrapper.pg_config)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO conversation_metadata (user_id, client_id, title, external_chat_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (external_chat_id) WHERE external_chat_id IS NOT NULL
                    DO UPDATE SET last_message_at = NOW()
                    RETURNING conversation_id
                    """,
                    (user_id, client_id, "Open WebUI Chat", external_chat_id)
                )
                conv_id = cursor.fetchone()[0]
                conn.commit()
                return conv_id
        finally:
            conn.close()

    except Exception as exc:
        logger.error(f"Failed to get/create conversation for {external_chat_id}: {exc}")
        return None
