import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from authlib.integrations.flask_client import OAuth
from flask import Flask, request as flask_request, jsonify, session, url_for

from src.archi.archi import archi
from src.archi.pipelines.agents.agent_spec import AgentSpecError, select_agent_spec
from src.data_manager.data_manager import DataManager
from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.utils.config_access import get_full_config
from src.utils.mattermost_auth import MattermostAuthManager
from src.utils.mattermost_token_service import MattermostTokenService
from src.utils.rbac.jwt_parser import get_user_roles
from src.utils.rbac.mattermost_context import get_mattermost_context, mattermost_user_context
from src.utils.rbac.registry import get_registry
from src.utils.rbac.permission_enum import Permission

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# MattermostClient — stateless REST API wrapper
# ---------------------------------------------------------------------------

class MattermostClient:
    """Stateless HTTP wrapper for the Mattermost REST API.

    Requires a Personal Access Token (PAK).  All methods raise
    requests.HTTPError on unexpected status codes; send_typing is
    best-effort and swallows exceptions.
    """

    def __init__(self, base_url: str, personal_access_token: str):
        self._base = base_url.rstrip('/')
        self._headers = {
            'Authorization': f'Bearer {personal_access_token}',
            'Content-Type': 'application/json',
        }

    def create_post(self, channel_id: str, message: str, root_id: str = "") -> dict:
        """Create a post.  Pass root_id to make it a thread reply."""
        payload: dict = {"channel_id": channel_id, "message": message}
        if root_id:
            payload["root_id"] = root_id
        r = requests.post(f"{self._base}/api/v4/posts", json=payload, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def get_thread(self, post_id: str) -> dict:
        """GET /api/v4/posts/{post_id}/thread — full thread with ordered post list."""
        r = requests.get(f"{self._base}/api/v4/posts/{post_id}/thread", headers=self._headers)
        r.raise_for_status()
        return r.json()

    def get_channel_posts(self, channel_id: str, per_page: int = 60, before: str = "") -> dict:
        """GET /api/v4/channels/{channel_id}/posts"""
        params: dict = {"per_page": per_page}
        if before:
            params["before"] = before
        r = requests.get(
            f"{self._base}/api/v4/channels/{channel_id}/posts",
            params=params,
            headers=self._headers,
        )
        r.raise_for_status()
        return r.json()

    def get_me(self) -> dict:
        """GET /api/v4/users/me — fetch the bot's own user info."""
        r = requests.get(f"{self._base}/api/v4/users/me", headers=self._headers)
        r.raise_for_status()
        return r.json()

    def add_reaction(self, user_id: str, post_id: str, emoji_name: str) -> None:
        """POST /api/v4/reactions"""
        payload = {"user_id": user_id, "post_id": post_id, "emoji_name": emoji_name}
        r = requests.post(f"{self._base}/api/v4/reactions", json=payload, headers=self._headers)
        r.raise_for_status()

    def delete_reaction(self, user_id: str, post_id: str, emoji_name: str) -> None:
        """DELETE /api/v4/users/{user_id}/posts/{post_id}/reactions/{emoji_name}"""
        r = requests.delete(
            f"{self._base}/api/v4/users/{user_id}/posts/{post_id}/reactions/{emoji_name}",
            headers=self._headers,
        )
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def send_typing(self, channel_id: str, parent_id: str = "") -> None:
        """POST /api/v4/users/me/typing — best-effort, swallows all exceptions."""
        payload: dict = {"channel_id": channel_id}
        if parent_id:
            payload["parent_id"] = parent_id
        try:
            requests.post(
                f"{self._base}/api/v4/users/me/typing",
                json=payload,
                headers=self._headers,
                timeout=3,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ThreadContextManager — per-thread conversation history via PostgreSQL
# ---------------------------------------------------------------------------

class ThreadContextManager:
    """Per-thread conversation history backed by PostgreSQL ConversationService.

    Mattermost messages are stored in the same `conversations` table as web-chat
    messages, keyed by a proper INTEGER `conversation_id` from `conversation_metadata`.
    A stable string `source_ref` (e.g. "mm_thread_<post_id>") is stored in
    `conversation_metadata.source_ref` so the same integer id is reused across restarts.

    The `client_id` stored in conversation_metadata is "mm_user_<username>", which
    the web-chat `list_conversations` endpoint uses to surface Mattermost conversations
    in the user's sidebar.
    """

    def __init__(self, conversation_service, bot_user_id: str = "", context_window: int = 20):
        self._svc = conversation_service
        self._bot_user_id = bot_user_id
        self._context_window = context_window
        # In-process cache: source_ref → integer conversation_id (avoids repeated DB lookups)
        self._id_cache: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Source-ref helpers (stable string keys used by the event handler)
    # ------------------------------------------------------------------

    @staticmethod
    def source_ref_for_thread(root_post_id: str) -> str:
        return f"mm_thread_{root_post_id}"

    @staticmethod
    def source_ref_for_user_channel(channel_id: str, user_id: str) -> str:
        """Fallback key for non-threaded messages: per-user per-channel conversation."""
        return f"mm_channel_{channel_id}_user_{user_id}"

    @staticmethod
    def mm_client_id(username: str) -> str:
        """Bridge key written into conversation_metadata.client_id.

        The web-chat list_conversations query includes ``client_id = 'mm_user_<username>'``
        so Mattermost conversations appear automatically in the authenticated user's sidebar.
        """
        return f"mm_user_{username}" if username else ""

    # ------------------------------------------------------------------
    # Integer ID resolution (creates conversation_metadata row on first use)
    # ------------------------------------------------------------------

    def _get_or_create_int_id(
        self, source_ref: str, username: str = "", title: str = ""
    ) -> Optional[int]:
        """Return the integer conversation_id for *source_ref*, creating if needed.

        Results are cached in-process to avoid redundant DB round-trips.
        Returns None only on DB error (caller falls back to stateless mode).
        """
        if source_ref in self._id_cache:
            return self._id_cache[source_ref]
        try:
            client_id = self.mm_client_id(username)
            int_id = self._svc.get_or_create_conversation_for_ref(
                source_ref=source_ref,
                mm_client_id=client_id,
                title=title,
            )
            self._id_cache[source_ref] = int_id
            return int_id
        except Exception as e:
            logger.warning("ThreadContextManager: could not resolve int id for %r: %s", source_ref, e)
            return None

    # ------------------------------------------------------------------
    # History retrieval
    # ------------------------------------------------------------------

    def build_history_from_db(
        self, source_ref: str, username: str = "", title: str = ""
    ) -> Tuple[Optional[int], List[Tuple[str, str]]]:
        """Return (int_conv_id, history) for *source_ref*.

        int_conv_id is the INTEGER primary key of the conversation_metadata row
        (used for storing the exchange).  history is a list of (role, content) tuples.
        """
        int_id = self._get_or_create_int_id(source_ref, username=username, title=title)
        if int_id is None:
            return None, []
        try:
            messages = self._svc.get_conversation_history(int_id, limit=self._context_window * 2)
        except Exception as e:
            logger.warning("ThreadContextManager: failed to fetch DB history for id=%s: %s", int_id, e)
            return int_id, []
        history = [
            ("User" if m.sender == "user" else "AI", m.content)
            for m in messages
        ]
        return int_id, history

    def build_history_from_thread(self, thread_data: dict) -> List[Tuple[str, str]]:
        """Cold-start: build history from a live Mattermost thread API response."""
        order = thread_data.get("order", [])
        posts = thread_data.get("posts", {})
        history = []
        for post_id in order:
            post = posts.get(post_id, {})
            if post.get("type", ""):  # skip system messages
                continue
            content = post.get("message", "").strip()
            if not content:
                continue
            role = "AI" if post.get("user_id") == self._bot_user_id else "User"
            history.append((role, content))
        return history[-(self._context_window * 2):]

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def store_exchange(
        self,
        conv_int_id: int,
        user_content: str,
        bot_content: str,
        model_used: Optional[str] = None,
        pipeline_used: Optional[str] = None,
    ) -> None:
        """Persist the user message and bot reply to PostgreSQL.

        Uses the INTEGER conv_int_id so the insert respects the FK constraint
        on conversations.conversation_id → conversation_metadata.conversation_id.
        """
        from datetime import datetime, timezone
        from src.utils.conversation_service import Message
        now = datetime.now(timezone.utc)
        try:
            self._svc.insert_messages([
                Message(
                    conversation_id=conv_int_id,
                    sender="user",
                    content=user_content,
                    ts=now,
                    archi_service="mattermost",
                ),
                Message(
                    conversation_id=conv_int_id,
                    sender="assistant",
                    content=bot_content,
                    ts=now,
                    model_used=model_used,
                    pipeline_used=pipeline_used,
                    archi_service="mattermost",
                ),
            ])
            # Keep last_message_at fresh so the conversation floats to the top
            # of the web-chat sidebar.
            self._svc.update_conversation_timestamp_for_ref(conv_int_id)
        except Exception as e:
            logger.warning("ThreadContextManager: failed to store exchange: %s", e)


# ---------------------------------------------------------------------------
# MattermostEventHandler — orchestrates auth → AI → thread reply for one post
# ---------------------------------------------------------------------------

class MattermostEventHandler:
    """Handles a single Mattermost post end-to-end.

    Used by both MattermostWebhookServer and Mattermost (polling) so that
    auth, AI, and reply logic is never duplicated.

    post_data dict keys: id, channel_id, root_id, user_id, username, message
    """

    def __init__(
        self,
        ai_wrapper,
        auth_manager: MattermostAuthManager,
        auth_enabled: bool,
        bot_user_id: str = "",
        mm_client: Optional[MattermostClient] = None,
        thread_ctx: Optional[ThreadContextManager] = None,
        webhook_url: Optional[str] = None,
        reactions: Optional[dict] = None,
        chat_base_url: str = "",
    ):
        self._ai = ai_wrapper
        self._auth_manager = auth_manager
        self._auth_enabled = auth_enabled
        self._bot_user_id = bot_user_id
        self._client = mm_client
        self._thread_ctx = thread_ctx
        self._webhook_url = webhook_url
        self._webhook_headers = {'Content-Type': 'application/json'}
        self._reactions = reactions or {
            "processing": "eyes",
            "done": "white_check_mark",
            "error": "x",
        }
        self._chat_base_url = chat_base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_message(self, channel_id: str, message: str, root_id: str = "") -> None:
        """Post a message, preferring thread-aware REST API, falling back to incoming webhook."""
        if self._client:
            try:
                self._client.create_post(channel_id, message, root_id=root_id)
                return
            except Exception as e:
                logger.warning("MattermostClient.create_post failed, falling back to webhook: %s", e)
        if self._webhook_url:
            try:
                requests.post(
                    self._webhook_url,
                    data=json.dumps({"text": message}),
                    headers=self._webhook_headers,
                )
            except Exception as e:
                logger.error("Webhook fallback also failed: %s", e)

    def _add_reaction(self, post_id: str, emoji: str) -> None:
        if self._client and self._bot_user_id:
            try:
                self._client.add_reaction(self._bot_user_id, post_id, emoji)
            except Exception as e:
                logger.warning("Could not add reaction %s: %s", emoji, e)

    def _delete_reaction(self, post_id: str, emoji: str) -> None:
        if self._client and self._bot_user_id:
            try:
                self._client.delete_reaction(self._bot_user_id, post_id, emoji)
            except Exception as e:
                logger.warning("Could not delete reaction %s: %s", emoji, e)

    def _send_typing(self, channel_id: str, root_id: str = "") -> None:
        if self._client:
            self._client.send_typing(channel_id, parent_id=root_id)

    def _notify_mcp_auth_needed(self, channel_id: str, username: str, root_id: str = "") -> None:
        """If any SSO-auth MCP servers lack a valid token for this user, send a one-line notice.

        Non-blocking: the AI call proceeds regardless — tools on unauthorized servers are simply
        unavailable for this message.  The notice is only sent when chat_base_url is configured.
        """
        if not self._chat_base_url or not username:
            return
        try:
            from src.utils.mcp_oauth_service import MCPOAuthService
            from src.utils.config_access import get_mcp_servers_config
            mcp_servers = get_mcp_servers_config()
            if not mcp_servers:
                return
            oauth_svc = MCPOAuthService()
            needing = oauth_svc.get_servers_needing_auth(username, mcp_servers)
            if needing:
                links = "  ".join(
                    f"[Authorize {n}]({self._chat_base_url}/mcp/authorize?server={n})"
                    for n in needing
                )
                self._send_message(
                    channel_id,
                    f":key: Some tools require authorization. Please visit the web chat to grant access: {links}",
                    root_id=root_id,
                )
        except Exception as e:
            logger.debug("MCP auth-needed check failed (non-fatal): %s", e)

    def _call_ai_with_retry(self, history: List[Tuple[str, str]], ctx, max_retries: int = 2) -> str:
        """Call archi with exponential-backoff retry on transient errors (e.g. OpenAI 500).

        Raises on the last attempt so the caller's except block can send the user an error.
        """
        import time
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(max_retries + 1):
            try:
                with mattermost_user_context(ctx):
                    result = self._ai.archi(
                        history=history,
                        user_id=ctx.username or None,
                    )
                return result["answer"]
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    wait = 2 ** attempt  # 1 s, 2 s
                    logger.warning(
                        "MattermostEventHandler: AI call failed (attempt %d/%d), "
                        "retrying in %ds: %s",
                        attempt + 1, max_retries + 1, wait, e,
                    )
                    time.sleep(wait)
        raise last_exc

    def _resolve_source_ref(
        self, post_id: str, root_id: str, channel_id: str, user_id: str
    ) -> str:
        """Return the stable source_ref string for this post.

        - Thread reply (root_id ≠ post_id): scoped to that thread so everyone
          in the thread shares context, and the web-chat user can continue there.
        - Root / non-threaded post: per-user-per-channel key so follow-up
          messages still carry history.
        """
        if self._thread_ctx is None:
            return ""
        if root_id and root_id != post_id:
            return ThreadContextManager.source_ref_for_thread(root_id)
        return ThreadContextManager.source_ref_for_user_channel(channel_id, user_id)

    def _build_history(
        self,
        post_id: str,
        root_id: str,
        channel_id: str,
        user_id: str,
        username: str,
        message: str,
    ) -> Tuple[Optional[int], List[Tuple[str, str]]]:
        """Return (conv_int_id, history) for this post.

        conv_int_id is the INTEGER primary key of the conversation_metadata row
        (None when ThreadContextManager is unavailable).  History is built from
        the DB first; falls back to the live Mattermost thread API on cold-start.
        """
        if self._thread_ctx is None:
            return None, [("User", message)]

        source_ref = self._resolve_source_ref(post_id, root_id, channel_id, user_id)
        conv_int_id, history = self._thread_ctx.build_history_from_db(
            source_ref, username=username, title=message[:50]
        )

        # Cold-start: seed history from live Mattermost thread (PAK required)
        if not history and root_id and root_id != post_id and self._client:
            try:
                thread_data = self._client.get_thread(root_id)
                history = self._thread_ctx.build_history_from_thread(thread_data)
            except Exception as e:
                logger.warning("Cold-start thread fetch failed: %s", e)

        return conv_int_id, history + [("User", message)]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def handle(self, post_data: dict) -> None:
        """Full handling pipeline for one Mattermost post.

        Steps: self-filter → auth → RBAC → typing → 👀 → build history
               → call AI → post reply in thread → store → ✅/❌
        """
        post_id    = post_data.get("id", "")
        channel_id = post_data.get("channel_id", "")
        # root_id for the thread — if empty this is a root post, reply starts a thread
        root_id    = post_data.get("root_id") or post_id
        user_id    = post_data.get("user_id", "")
        username   = post_data.get("username", "") or post_data.get("user_name", "")
        message    = post_data.get("message", "").strip()

        # 1. Skip the bot's own posts (prevents infinite-loop on self-replies)
        if self._bot_user_id and user_id == self._bot_user_id:
            return

        # 2. Auth: build user context (None → no stored token → prompt login)
        ctx = self._auth_manager.build_context(user_id=user_id, username=username)
        if ctx is None:
            login_url = self._auth_manager.login_url(user_id, username)
            self._send_message(
                channel_id,
                f"Hi @{username}! To use this bot, please login first: {login_url}\n"
                "After logging in, send your message again.",
                root_id=root_id,
            )
            return

        # 3. RBAC permission check
        if self._auth_enabled:
            registry = get_registry()
            if not registry.has_permission(ctx.roles, Permission.Mattermost.ACCESS):
                logger.info(
                    "MattermostEventHandler: access denied for user_id=%r (roles=%s)",
                    user_id, ctx.roles,
                )
                self._send_message(
                    channel_id,
                    "Sorry, you don't have permission to use this bot. Please contact an administrator.",
                    root_id=root_id,
                )
                return

        # 3.5. Notify user about MCP servers that still need OAuth authorization.
        #      This is informational-only — the AI call proceeds with available tools.
        self._notify_mcp_auth_needed(channel_id, ctx.username, root_id=root_id)

        logger.info(
            "MattermostEventHandler: message from @%s (id=%s, channel=%s): %r",
            username, user_id, channel_id, message,
        )

        # 4. Typing indicator (best-effort, before the long AI call)
        self._send_typing(channel_id, root_id=root_id)

        # 5. 👀 reaction to acknowledge receipt
        self._add_reaction(post_id, self._reactions["processing"])

        try:
            # 6. Build conversation history (DB primary, cold-start from thread API).
            # conv_int_id is the INTEGER pk of conversation_metadata (None = no storage).
            conv_int_id, history = self._build_history(
                post_id, root_id, channel_id, user_id, username, message
            )

            # 7. Call AI pipeline — retry up to 2x on transient upstream errors (e.g. OpenAI 500)
            answer = self._call_ai_with_retry(history, ctx)
            logger.debug("MattermostEventHandler: ANSWER = %s", answer)

            # 8. Post reply in thread
            self._send_message(channel_id, answer, root_id=root_id)

            # 9. Store exchange in PostgreSQL (conv_int_id is None without storage layer)
            if self._thread_ctx and conv_int_id is not None:
                self._thread_ctx.store_exchange(conv_int_id, message, answer)

            # 10a. ✅ reaction, remove 👀
            self._delete_reaction(post_id, self._reactions["processing"])
            self._add_reaction(post_id, self._reactions["done"])

        except Exception as e:
            logger.error(
                "MattermostEventHandler: failed to handle post %s: %s", post_id, e, exc_info=True
            )
            try:
                self._send_message(
                    channel_id,
                    "Sorry, I encountered an error processing your message. Please try again.",
                    root_id=root_id,
                )
            except Exception:
                pass
            # 10b. ❌ reaction, remove 👀
            self._delete_reaction(post_id, self._reactions["processing"])
            self._add_reaction(post_id, self._reactions["error"])


# ---------------------------------------------------------------------------
# MattermostAIWrapper — initializes and calls the archi pipeline
# ---------------------------------------------------------------------------

class MattermostAIWrapper:
    def __init__(self):
        # initialize and update vector store
        self.data_manager = DataManager(run_ingestion=False)

        # initialize chain
        config = get_full_config()
        services_cfg = config.get("services", {})
        mm_cfg = services_cfg.get("mattermost", {})
        chat_cfg = services_cfg.get("chat_app", {})
        agent_class = mm_cfg.get("agent_class") or chat_cfg.get("agent_class", "QAPipeline")
        agents_dir = mm_cfg.get("agents_dir") or chat_cfg.get("agents_dir")
        agent_spec = None
        if agents_dir:
            try:
                agent_spec = select_agent_spec(Path(agents_dir))
            except AgentSpecError as exc:
                logger.warning(f"Failed to load agent spec: {exc}")
                agent_spec = None
        prompt_overrides = mm_cfg.get("prompts", {})
        self.archi = archi(
            pipeline=agent_class,
            agent_spec=agent_spec,
            default_provider=mm_cfg.get("default_provider") or chat_cfg.get("default_provider"),
            default_model=mm_cfg.get("default_model") or chat_cfg.get("default_model"),
            prompt_overrides=prompt_overrides,
        )

    def call_with_history(
        self, history: List[Tuple[str, str]], user_id: Optional[str] = None
    ) -> str:
        """Call archi with explicit multi-turn history. Returns answer string."""
        answer = self.archi(history=history, user_id=user_id)["answer"]
        logger.debug('ANSWER = %s', answer)
        return answer

    def __call__(self, post):
        # Build single-turn history from post dict (backward-compatible path)
        post_str = post['message']
        formatted_history = [("User", post_str)]

        # Resolve user_id for MCP tool authentication.
        # ctx.username matches the CERN SSO preferred_username / sub used as
        # the key in mcp_oauth_tokens, enabling cmspnr tools for authed users.
        user_id = None
        try:
            mm_ctx = get_mattermost_context()
            if mm_ctx is not None:
                user_id = mm_ctx.username or None
        except Exception:
            pass

        answer = self.archi(history=formatted_history, user_id=user_id)["answer"]
        logger.debug('ANSWER = %s', answer)
        return answer, post_str


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------

def _derive_chat_base_url(config: dict) -> str:
    """Derive the web-chat base URL from config so Mattermost can send MCP auth links.

    Uses services.mcp_server.url (already the public host) and appends
    services.chat_app.external_port.  Falls back to services.mattermost.auth.login_base_url
    with the chat port substituted, then empty string if nothing is available.
    """
    from urllib.parse import urlparse
    try:
        services = config.get("services", {})
        chat_port = int(services.get("chat_app", {}).get("external_port", 0))
        mcp_srv_url = services.get("mcp_server", {}).get("url", "")
        if mcp_srv_url and chat_port:
            parsed = urlparse(mcp_srv_url)
            return f"{parsed.scheme}://{parsed.hostname}:{chat_port}"
        # Fallback: derive from Mattermost login_base_url by swapping port
        login_url = services.get("mattermost", {}).get("auth", {}).get("login_base_url", "")
        if login_url and chat_port:
            parsed = urlparse(login_url)
            return f"{parsed.scheme}://{parsed.hostname}:{chat_port}"
    except Exception:
        pass
    return ""


def _build_mm_client_and_context(
    mm_config: dict,
    mattermost_url: str,
) -> Tuple[Optional[MattermostClient], Optional[ThreadContextManager], str]:
    """Create MattermostClient + ThreadContextManager if PAK is available.

    Returns (mm_client, thread_ctx, bot_user_id).
    bot_user_id comes from config, or is auto-fetched from the API if blank.
    """
    pak = read_secret("MATTERMOST_PAK")
    bot_user_id = mm_config.get("bot_user_id", "")

    mm_client: Optional[MattermostClient] = None
    if pak:
        mm_client = MattermostClient(mattermost_url, pak)
        # Auto-fetch bot user ID if not explicitly configured
        if not bot_user_id:
            try:
                me = mm_client.get_me()
                bot_user_id = me.get("id", "")
                logger.info("MattermostClient: auto-fetched bot_user_id=%r", bot_user_id)
            except Exception as e:
                logger.warning("MattermostClient: could not auto-fetch bot user ID: %s", e)

    # ThreadContextManager is always created when PostgreSQL is available —
    # it handles conversation storage/continuity independently of the PAK.
    thread_ctx: Optional[ThreadContextManager] = None
    try:
        from src.utils.postgres_service_factory import PostgresServiceFactory
        factory = PostgresServiceFactory.get_instance()
        if factory is not None:
            thread_ctx = ThreadContextManager(
                conversation_service=factory.conversation_service,
                bot_user_id=bot_user_id,
                context_window=int(mm_config.get("context_window", 20)),
            )
            logger.info(
                "MattermostClient: thread context manager initialised "
                "(context_window=%s, pak=%s)",
                mm_config.get("context_window", 20),
                "yes" if pak else "no",
            )
    except Exception as e:
        logger.warning("Could not initialise ThreadContextManager: %s", e)

    return mm_client, thread_ctx, bot_user_id


# ---------------------------------------------------------------------------
# Mattermost — polling mode
# ---------------------------------------------------------------------------

class Mattermost:
    """
    Polling-based Mattermost integration.
    Periodically fetches new posts from a channel and replies via the event handler.
    """
    def __init__(self):
        logger.info('Mattermost::INIT')

        config = get_full_config()
        self.mattermost_config = config.get("services", {}).get("mattermost", {})
        mm_config = self.mattermost_config or {}

        # Auth setup
        auth_config = mm_config.get("auth", {})
        pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **config.get("services", {}).get("postgres", {}),
        }
        auth_manager = MattermostAuthManager(auth_config, pg_config=pg_config)
        auth_enabled = auth_config.get("enabled", False)

        # Mattermost connection details
        self.mattermost_url = mm_config.get("base_url", "https://mattermost.web.cern.ch/")
        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.mattermost_channel_id_read = read_secret("MATTERMOST_CHANNEL_ID_READ")
        self.mattermost_channel_id_write = read_secret("MATTERMOST_CHANNEL_ID_WRITE")
        self.PAK = read_secret("MATTERMOST_PAK")
        self.mattermost_headers = {
            'Authorization': f'Bearer {self.PAK}',
            'Content-Type': 'application/json',
        }

        logger.debug('mattermost_webhook = %s', self.mattermost_webhook)
        logger.debug('mattermost_channel_id_read = %s', self.mattermost_channel_id_read)
        logger.debug('mattermost_channel_id_write = %s', self.mattermost_channel_id_write)
        logger.debug('PAK = %s', self.PAK)

        # Tracking file for deduplication (config-driven, safe default)
        self.min_next_post_file = mm_config.get(
            "tracking_file", "/root/data/mattermost/answered_posts.json"
        )

        # AI wrapper
        ai_wrapper = MattermostAIWrapper()

        # MattermostClient + ThreadContextManager (PAK-gated)
        mm_client, thread_ctx, bot_user_id = _build_mm_client_and_context(
            mm_config, self.mattermost_url
        )

        # Event handler — consolidates all auth + AI + reply logic
        self.event_handler = MattermostEventHandler(
            ai_wrapper=ai_wrapper,
            auth_manager=auth_manager,
            auth_enabled=auth_enabled,
            bot_user_id=bot_user_id,
            mm_client=mm_client,
            thread_ctx=thread_ctx,
            webhook_url=self.mattermost_webhook,
            reactions=mm_config.get("reactions"),
            chat_base_url=_derive_chat_base_url(config),
        )

    def write_min_next_post(self, answered_key):
        try:
            os.makedirs(os.path.dirname(self.min_next_post_file), exist_ok=True)
            with open(self.min_next_post_file, "w") as f:
                json.dump({"answered_id": answered_key}, f)
            logger.info(f"Updated answered_id {answered_key}")
        except Exception as e:
            logger.debug(f"ERROR - Failed to write answered_key to file: {e}")

    def get_active_posts(self):
        content = f"api/v4/channels/{self.mattermost_channel_id_read}/posts"
        r = requests.get(self.mattermost_url + content, headers=self.mattermost_headers)
        active_posts = {}
        for id in r.json()["order"]:
            active_posts[id] = r.json()["posts"][id]["message"]
        return active_posts

    def filter_posts(self, posts, excluded_user_id):
        system_types = {
            "system_join_team",
            "system_join_channel",
            "system_add_to_channel",
            "system_leave_team",
            "system_leave_channel",
            "system_remove_from_channel",
        }
        filtered = []
        for post in posts.values():
            if post.get("user_id") == excluded_user_id:
                continue
            if post.get("type") in system_types:
                continue
            filtered.append(post)
        return filtered

    def get_last_post(self):
        content = f"api/v4/channels/{self.mattermost_channel_id_read}/posts"
        r = requests.get(self.mattermost_url + content, headers=self.mattermost_headers)
        data = r.json()
        posts = data.get('posts', {})

        # bot_user_id comes from event_handler (config-driven or auto-fetched)
        excluded_bot_id = self.event_handler._bot_user_id

        filtered_posts = self.filter_posts(posts, excluded_user_id=excluded_bot_id)
        sorted_posts = sorted(filtered_posts, key=lambda x: x['create_at'], reverse=True)

        if sorted_posts:
            latest = sorted_posts[0]
            logger.info(f"User ID: {latest['user_id']}")
            logger.info(f"Message: {latest['message']}")
        else:
            logger.debug("Mattermost: No messages found.")

        return sorted_posts[0]

    def checkAnswerExist(self, tmpID):
        if not os.path.exists(self.min_next_post_file):
            logger.info("File does not exist, creating new one.")
            return False
        else:
            with open(self.min_next_post_file, "r") as f:
                data = json.load(f)
                logger.info("Loaded data: %s", data)

        answered_ids = data.get("answered_id", [])

        if not isinstance(answered_ids, list):
            answered_ids = [answered_ids]

        if tmpID in answered_ids:
            logger.info(f"{tmpID} already exists")
            return True
        else:
            answered_ids.append(tmpID)
            data["answered_id"] = answered_ids
            with open(self.min_next_post_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Added {tmpID} for next iterations")
            return False

    # for now just processes "main" posts, i.e. not replies/follow-ups
    def process_posts(self):
        try:
            topic = self.get_last_post()
        except Exception as e:
            logger.error("ERROR - Failed to parse feed due to the following exception:")
            logger.error(str(e))
            return

        if self.checkAnswerExist(topic['id']):
            logger.info('no need to answer someone already answered')
            return

        post_data = {
            "id":         topic.get("id", ""),
            "channel_id": topic.get("channel_id", self.mattermost_channel_id_read),
            "root_id":    topic.get("root_id", ""),
            "user_id":    topic.get("user_id", ""),
            "username":   topic.get("username", ""),
            "message":    topic.get("message", ""),
        }

        try:
            self.event_handler.handle(post_data)
            self.write_min_next_post(topic['id'])
        except Exception as e:
            logger.error(
                f"ERROR - Failed to process post {topic['id']} due to the following exception:"
            )
            logger.error(str(e))


# ---------------------------------------------------------------------------
# MattermostWebhookServer — event-driven (Flask) mode
# ---------------------------------------------------------------------------

class MattermostWebhookServer:
    """
    Event-driven alternative to the polling-based Mattermost class.
    Runs a Flask HTTP server that receives messages via an outgoing webhook
    (Mattermost pushes POSTs here) and replies via the REST API or incoming webhook.
    No Personal Access Token required for basic operation; PAK enables thread
    replies, reactions, and typing indicators.
    """
    def __init__(self):
        logger.info('MattermostWebhookServer::INIT')

        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.outgoing_token = read_secret("MATTERMOST_OUTGOING_TOKEN")

        config = get_full_config()
        mm_config = config.get("services", {}).get("mattermost", {})
        self.port = int(mm_config.get("port", 5000))
        self.mattermost_url = mm_config.get("base_url", "https://mattermost.web.cern.ch/")

        # Auth setup
        auth_config = mm_config.get("auth", {})
        pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **config.get("services", {}).get("postgres", {}),
        }
        auth_manager = MattermostAuthManager(auth_config, pg_config=pg_config)
        auth_enabled = auth_config.get("enabled", False)

        # AI wrapper
        ai_wrapper = MattermostAIWrapper()

        # MattermostClient + ThreadContextManager (PAK-gated)
        mm_client, thread_ctx, bot_user_id = _build_mm_client_and_context(
            mm_config, self.mattermost_url
        )

        # Event handler
        self.event_handler = MattermostEventHandler(
            ai_wrapper=ai_wrapper,
            auth_manager=auth_manager,
            auth_enabled=auth_enabled,
            bot_user_id=bot_user_id,
            mm_client=mm_client,
            thread_ctx=thread_ctx,
            webhook_url=self.mattermost_webhook,
            reactions=mm_config.get("reactions"),
            chat_base_url=_derive_chat_base_url(config),
        )

        import secrets as _secrets
        self.app = Flask(__name__)
        self.app.secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY") or _secrets.token_hex(32)
        self.app.add_url_rule('/webhook', 'webhook', self._handle_webhook, methods=['POST'])

        # SSO OAuth routes for Mattermost user authentication
        sso_cfg = auth_config.get('sso', {})
        self._sso_enabled = bool(read_secret("SSO_CLIENT_ID") and read_secret("SSO_CLIENT_SECRET"))
        if self._sso_enabled:
            self._oauth = OAuth(self.app)
            self._oauth.register(
                name='sso',
                client_id=read_secret("SSO_CLIENT_ID"),
                client_secret=read_secret("SSO_CLIENT_SECRET"),
                server_metadata_url=sso_cfg.get(
                    'server_metadata_url',
                    'https://auth.cern.ch/auth/realms/cern/.well-known/openid-configuration',
                ),
                client_kwargs={'scope': 'openid profile email offline_access'},
            )
            self._token_service = MattermostTokenService(
                pg_config=pg_config,
                token_endpoint=sso_cfg.get('token_endpoint', ''),
                session_lifetime_days=int(auth_config.get('session_lifetime_days', 30)),
                roles_refresh_hours=int(auth_config.get('roles_refresh_hours', 24)),
            )
            self.app.add_url_rule('/mattermost-auth', 'mattermost_auth_login', self._mattermost_auth_login)
            self.app.add_url_rule('/mattermost-auth/callback', 'mattermost_auth_callback', self._mattermost_auth_callback)
            logger.info("MattermostWebhookServer: SSO auth routes registered")

    def _handle_webhook(self):
        # Mattermost outgoing webhooks send either application/x-www-form-urlencoded or application/json
        if flask_request.is_json:
            data = flask_request.get_json(silent=True) or {}
        else:
            data = flask_request.form

        token = data.get('token', '')
        if self.outgoing_token and token != self.outgoing_token:
            logger.warning('MattermostWebhookServer: received request with invalid token')
            return jsonify({}), 403

        text = data.get('text', '').strip()
        if not text:
            return jsonify({}), 200

        # Build a normalised post_data dict from the outgoing webhook payload.
        # Mattermost outgoing webhook fields: post_id, root_id, user_id, user_name,
        # channel_id, text, token, team_id, etc.
        post_data = {
            "id":         data.get("post_id", ""),
            "channel_id": data.get("channel_id", ""),
            "root_id":    data.get("root_id", ""),   # non-empty if this post is a thread reply
            "user_id":    data.get("user_id", ""),
            "username":   data.get("user_name", ""),
            "message":    text,
        }

        # Process in a background thread — Mattermost outgoing webhooks have a ~5 second
        # timeout and will retry on no response, causing duplicate AI calls. We must return
        # 200 immediately and do the AI work asynchronously.
        threading.Thread(
            target=self.event_handler.handle,
            args=(post_data,),
            daemon=True,
        ).start()

        # Without PAK, reactions and typing indicators are unavailable.
        # Return a text acknowledgment in the HTTP response body instead —
        # Mattermost posts this text to the channel immediately as visual feedback.
        if not self.event_handler._client:
            return jsonify({"text": ":hourglass_flowing_sand: _Processing..._"}), 200

        return jsonify({}), 200

    def _mattermost_auth_login(self):
        """
        Step 1: user clicks the login link from Mattermost.
        Stashes mm_username in session, then redirects to CERN SSO.
        mm_user_id is passed as OAuth state and round-tripped back by SSO.
        """
        mm_user_id = flask_request.args.get('state', '').strip()
        mm_username = flask_request.args.get('username', '').strip()
        if not mm_user_id:
            return "Missing Mattermost user ID", 400
        session['_mm_pending_username'] = mm_username
        redirect_uri = url_for('mattermost_auth_callback', _external=True)
        return self._oauth.sso.authorize_redirect(redirect_uri, state=mm_user_id)

    def _mattermost_auth_callback(self):
        """
        Step 2: CERN SSO redirects back here after the user authenticates.
        Extracts roles from the JWT and stores them in mattermost_tokens.
        """
        try:
            token = self._oauth.sso.authorize_access_token()
            mm_user_id = flask_request.args.get('state', '').strip()
            mm_username = session.pop('_mm_pending_username', '')

            if not mm_user_id:
                return "Missing Mattermost user ID in callback state", 400

            user_info = token.get('userinfo') or self._oauth.sso.userinfo(token=token)
            user_email = user_info.get('email', user_info.get('preferred_username', ''))
            user_roles = get_user_roles(token, user_email)

            self._token_service.store_token(
                mm_user_id=mm_user_id,
                mm_username=mm_username or user_info.get('preferred_username', ''),
                email=user_email,
                roles=user_roles,
                refresh_token=token.get('refresh_token'),
            )

            logger.info(
                f"Mattermost auth successful: @{mm_username} (id={mm_user_id!r}) "
                f"email={user_email!r} roles={user_roles}"
            )
            return (
                "<html><body style='font-family:sans-serif;padding:2em'>"
                "<h2>Login successful!</h2>"
                f"<p>You are now authenticated as <strong>{user_email}</strong> "
                f"with roles: <strong>{', '.join(user_roles)}</strong>.</p>"
                "<p>You can close this tab and return to Mattermost.</p>"
                "</body></html>"
            )
        except Exception as exc:
            logger.error(f"Mattermost auth callback error: {exc}")
            return (
                "<html><body style='font-family:sans-serif;padding:2em'>"
                "<h2>Authentication failed</h2>"
                f"<p>Error: {exc}</p>"
                "<p>Please try clicking the login link in Mattermost again.</p>"
                "</body></html>"
            ), 500

    def run(self, host='0.0.0.0', port=5000):
        logger.info(f'MattermostWebhookServer: starting on {host}:{port}')
        self.app.run(host=host, port=port)
