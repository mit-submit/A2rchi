"""
SSOTokenService - DB-backed access/refresh token store for web SSO users.

Mirrors the pattern from MattermostTokenService but for the main chat_app's
SSO flow. Stores pgp-encrypted tokens in PostgreSQL, refreshes access tokens
silently when they expire, so MCP servers can use per-user Bearer auth
without relying on short-lived Flask session state.

Session lifetime:  configurable (default 30 days) — full re-login required
Access token:      refreshed silently via refresh_token when expired
"""

import requests as http_requests
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SSOTokenService:
    """
    DB-backed token store for chat_app SSO auth.

    Stores encrypted access_token + refresh_token per user_id (the SSO 'sub'
    claim). When the access_token has expired, silently exchanges the
    refresh_token for a new one. Full re-login is only required when the
    refresh_token itself expires (session_lifetime_days).
    """

    def __init__(
        self,
        pg_config: dict = None,
        token_endpoint: str = "",
        session_lifetime_days: int = 30,
    ):
        self.pg_config = pg_config or {}
        self.token_endpoint = token_endpoint
        self.session_lifetime_days = session_lifetime_days
        self._encryption_key = (
            read_secret("BYOK_ENCRYPTION_KEY")
            or read_secret("PG_ENCRYPTION_KEY")
            or read_secret("UPLOADER_SALT")
            or read_secret("FLASK_UPLOADER_APP_SECRET_KEY")
        )
        if not self._encryption_key:
            logger.warning(
                "SSOTokenService: no encryption key found "
                "(BYOK_ENCRYPTION_KEY / PG_ENCRYPTION_KEY / UPLOADER_SALT). "
                "SSO tokens will not be persisted — MCP sso_auth servers will be skipped."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store_token(
        self,
        user_id: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_in: int = 300,
    ) -> None:
        """Persist access + refresh tokens after a successful SSO login."""
        if not self._encryption_key:
            return

        now = datetime.now(timezone.utc)
        access_expires_at = now + timedelta(seconds=expires_in)
        session_expires_at = now + timedelta(days=self.session_lifetime_days)

        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO sso_tokens
                            (user_id, access_token, refresh_token,
                             access_token_expires_at, session_expires_at, updated_at)
                        VALUES (%s,
                                pgp_sym_encrypt(%s, %s),
                                pgp_sym_encrypt(%s, %s),
                                %s, %s, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                            access_token            = EXCLUDED.access_token,
                            refresh_token           = EXCLUDED.refresh_token,
                            access_token_expires_at = EXCLUDED.access_token_expires_at,
                            session_expires_at      = EXCLUDED.session_expires_at,
                            updated_at              = NOW()
                        """,
                        (
                            user_id,
                            access_token, self._encryption_key,
                            refresh_token or "", self._encryption_key,
                            access_expires_at, session_expires_at,
                        ),
                    )
                conn.commit()
            logger.info(
                f"Stored SSO tokens for user_id={user_id!r}, "
                f"access_expires={access_expires_at.isoformat()}"
            )
        except Exception as exc:
            logger.error(f"Failed to store SSO token for user_id={user_id!r}: {exc}")

    def get_access_token(self, user_id: str) -> Optional[str]:
        """
        Return a valid access token for the user.

        - Returns the stored token if it hasn't expired.
        - Silently refreshes via refresh_token if the access token is stale.
        - Returns None if no token is stored, the session has expired, or
          the refresh fails (user must re-login).
        """
        if not user_id or not self._encryption_key:
            return None

        row = self._fetch_row(user_id)
        if row is None:
            logger.debug(f"No SSO token stored for user_id={user_id!r}")
            return None

        access_token, refresh_token, access_expires_at, session_expires_at = row
        now = datetime.now(timezone.utc)

        # Hard session expiry — full re-login required
        if session_expires_at and now > session_expires_at:
            logger.info(f"SSO session expired for user_id={user_id!r}, invalidating")
            self.invalidate(user_id)
            return None

        # Access token still valid
        if access_expires_at and now < access_expires_at:
            return access_token

        # Access token expired — try silent refresh
        logger.info(f"Access token expired for user_id={user_id!r}, refreshing")
        return self._refresh_access_token(user_id, refresh_token)

    def invalidate(self, user_id: str) -> None:
        """Delete stored tokens (e.g. on logout or hard session expiry)."""
        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM sso_tokens WHERE user_id = %s", (user_id,))
                conn.commit()
            logger.info(f"Invalidated SSO tokens for user_id={user_id!r}")
        except Exception as exc:
            logger.warning(f"Failed to invalidate SSO token for user_id={user_id!r}: {exc}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_pool(self):
        from src.utils.postgres_service_factory import PostgresServiceFactory
        factory = PostgresServiceFactory.get_instance()
        if factory:
            return factory.connection_pool
        from src.utils.connection_pool import ConnectionPool
        return ConnectionPool(connection_params=self.pg_config)

    def _fetch_row(self, user_id: str):
        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pgp_sym_decrypt(access_token,  %s)::text,
                               pgp_sym_decrypt(refresh_token, %s)::text,
                               access_token_expires_at,
                               session_expires_at
                        FROM sso_tokens
                        WHERE user_id = %s
                        """,
                        (self._encryption_key, self._encryption_key, user_id),
                    )
                    return cur.fetchone()
        except Exception as exc:
            logger.warning(f"Failed to fetch SSO token for user_id={user_id!r}: {exc}")
            return None

    def _refresh_access_token(self, user_id: str, refresh_token: Optional[str]) -> Optional[str]:
        if not refresh_token or not self.token_endpoint:
            return None

        client_id = read_secret("SSO_CLIENT_ID")
        client_secret = read_secret("SSO_CLIENT_SECRET")
        if not client_id or not client_secret:
            return None

        try:
            resp = http_requests.post(
                self.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=10,
            )
            resp.raise_for_status()
            new_token = resp.json()
        except Exception as exc:
            logger.warning(f"Token refresh HTTP error for user_id={user_id!r}: {exc}")
            return None

        new_access = new_token.get("access_token")
        new_refresh = new_token.get("refresh_token") or refresh_token
        expires_in = int(new_token.get("expires_in", 300))

        if new_access:
            self.store_token(user_id, new_access, new_refresh, expires_in)

        return new_access
