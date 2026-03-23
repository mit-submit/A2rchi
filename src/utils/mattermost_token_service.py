"""
Mattermost Token Service - Manages SSO tokens for Mattermost users.

Stores SSO refresh tokens and roles in PostgreSQL, enabling role-based access
without requiring re-login on every message. Silently refreshes roles using the
stored refresh token; only prompts the user to re-login when the session expires.

Session lifetime:  configurable (default 30 days) — full re-login required
Roles refresh:     configurable (default 24h) — silent, uses refresh token
"""

import json
import requests as http_requests
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.utils.rbac.jwt_parser import get_user_roles
from src.utils.rbac.mattermost_context import MattermostUserContext

logger = get_logger(__name__)


class MattermostTokenService:
    """
    DB-backed token store for Mattermost SSO auth.

    Initialized with PostgreSQL config and OIDC token endpoint. The token
    endpoint is used for silent role refresh via refresh_token grant.
    """

    def __init__(
        self,
        pg_config: dict,
        token_endpoint: str = "",
        session_lifetime_days: int = 30,
        roles_refresh_hours: int = 24,
    ):
        self.pg_config = pg_config
        self.token_endpoint = token_endpoint
        self.session_lifetime_days = session_lifetime_days
        self.roles_refresh_hours = roles_refresh_hours
        self._encryption_key = read_secret("BYOK_ENCRYPTION_KEY") or read_secret("PG_ENCRYPTION_KEY")
        if not self._encryption_key:
            logger.warning(
                "MattermostTokenService: no encryption key found (BYOK_ENCRYPTION_KEY). "
                "Refresh tokens will not be stored — silent role refresh disabled."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_user_context(
        self, mm_user_id: str, mm_username: str = ""
    ) -> Optional[MattermostUserContext]:
        """
        Return a MattermostUserContext for a known user, or None if:
        - No token is stored (user must login)
        - Session has expired (user must re-login)

        Silently refreshes roles if they are stale (older than roles_refresh_hours).
        """
        row = self._fetch_row(mm_user_id)
        if row is None:
            logger.debug(f"No token for Mattermost user_id={mm_user_id!r}")
            return None

        stored_username, email, roles, token_expires_at, roles_refreshed_at, refresh_token = row

        # Session expiry check — requires full re-login
        now = datetime.now(timezone.utc)
        if token_expires_at and now > token_expires_at:
            logger.info(f"Session expired for Mattermost user_id={mm_user_id!r}, invalidating")
            self.invalidate(mm_user_id)
            return None

        # Silent role refresh if stale
        if roles_refreshed_at:
            stale_threshold = now - timedelta(hours=self.roles_refresh_hours)
            if roles_refreshed_at < stale_threshold:
                logger.info(f"Refreshing stale roles for Mattermost user_id={mm_user_id!r}")
                fresh = self._refresh_roles(mm_user_id, email or "", refresh_token)
                if fresh is not None:
                    roles = fresh
                else:
                    logger.warning(
                        f"Role refresh failed for user_id={mm_user_id!r}, using cached roles"
                    )

        return MattermostUserContext(
            user_id=mm_user_id,
            username=mm_username or stored_username or "",
            roles=roles,
            email=email or "",
        )

    def store_token(
        self,
        mm_user_id: str,
        mm_username: str,
        email: str,
        roles: List[str],
        refresh_token: Optional[str],
    ) -> None:
        """Store or update a token for a Mattermost user."""
        expires_at = datetime.now(timezone.utc) + timedelta(days=self.session_lifetime_days)
        pool = self._get_pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                if self._encryption_key and refresh_token:
                    cur.execute(
                        """
                        INSERT INTO mattermost_tokens
                            (mattermost_user_id, mattermost_username, email, roles,
                             refresh_token, token_expires_at, roles_refreshed_at, updated_at)
                        VALUES (%s, %s, %s, %s,
                                pgp_sym_encrypt(%s, %s), %s, NOW(), NOW())
                        ON CONFLICT (mattermost_user_id) DO UPDATE SET
                            mattermost_username = EXCLUDED.mattermost_username,
                            email               = EXCLUDED.email,
                            roles               = EXCLUDED.roles,
                            refresh_token       = EXCLUDED.refresh_token,
                            token_expires_at    = EXCLUDED.token_expires_at,
                            roles_refreshed_at  = NOW(),
                            updated_at          = NOW()
                        """,
                        (mm_user_id, mm_username, email, json.dumps(roles),
                         refresh_token, self._encryption_key, expires_at),
                    )
                else:
                    # No encryption key or no refresh token — store without refresh capability
                    cur.execute(
                        """
                        INSERT INTO mattermost_tokens
                            (mattermost_user_id, mattermost_username, email, roles,
                             token_expires_at, roles_refreshed_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (mattermost_user_id) DO UPDATE SET
                            mattermost_username = EXCLUDED.mattermost_username,
                            email               = EXCLUDED.email,
                            roles               = EXCLUDED.roles,
                            token_expires_at    = EXCLUDED.token_expires_at,
                            roles_refreshed_at  = NOW(),
                            updated_at          = NOW()
                        """,
                        (mm_user_id, mm_username, email, json.dumps(roles), expires_at),
                    )
            conn.commit()
        logger.info(
            f"Stored token for Mattermost @{mm_username} (id={mm_user_id!r}), "
            f"roles={roles}, expires={expires_at.date()}"
        )

    def invalidate(self, mm_user_id: str) -> None:
        """Delete the stored token for a user, forcing re-login on next message."""
        pool = self._get_pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM mattermost_tokens WHERE mattermost_user_id = %s",
                    (mm_user_id,),
                )
            conn.commit()
        logger.info(f"Invalidated Mattermost token for user_id={mm_user_id!r}")

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

    def _fetch_row(self, mm_user_id: str):
        """Fetch token row from DB. Returns tuple or None."""
        pool = self._get_pool()
        with pool.get_connection() as conn:
            with conn.cursor() as cur:
                if self._encryption_key:
                    cur.execute(
                        """
                        SELECT mattermost_username, email, roles,
                               token_expires_at, roles_refreshed_at,
                               pgp_sym_decrypt(refresh_token, %s)::text AS refresh_token
                        FROM mattermost_tokens
                        WHERE mattermost_user_id = %s
                        """,
                        (self._encryption_key, mm_user_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT mattermost_username, email, roles,
                               token_expires_at, roles_refreshed_at,
                               NULL AS refresh_token
                        FROM mattermost_tokens
                        WHERE mattermost_user_id = %s
                        """,
                        (mm_user_id,),
                    )
                row = cur.fetchone()

        if row is None:
            return None

        stored_username, email, roles_raw, token_expires_at, roles_refreshed_at, refresh_token = row
        roles = json.loads(roles_raw) if isinstance(roles_raw, str) else (roles_raw or [])
        return stored_username, email, roles, token_expires_at, roles_refreshed_at, refresh_token

    def _refresh_roles(
        self, mm_user_id: str, email: str, refresh_token: Optional[str]
    ) -> Optional[List[str]]:
        """
        Exchange refresh token for a new token, extract fresh roles, update DB.
        Returns new roles on success, None on failure.
        """
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
            logger.warning(f"Token refresh HTTP error for user_id={mm_user_id!r}: {exc}")
            return None

        try:
            fresh_roles = get_user_roles(new_token, email)
        except Exception as exc:
            logger.warning(f"Role extraction failed for user_id={mm_user_id!r}: {exc}")
            return None

        # Update DB — new refresh token if provided
        new_refresh = new_token.get("refresh_token") or refresh_token
        pool = self._get_pool()
        try:
            with pool.get_connection() as conn:
                with conn.cursor() as cur:
                    if self._encryption_key and new_refresh:
                        cur.execute(
                            """
                            UPDATE mattermost_tokens
                            SET roles = %s, roles_refreshed_at = NOW(),
                                refresh_token = pgp_sym_encrypt(%s, %s), updated_at = NOW()
                            WHERE mattermost_user_id = %s
                            """,
                            (json.dumps(fresh_roles), new_refresh,
                             self._encryption_key, mm_user_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE mattermost_tokens
                            SET roles = %s, roles_refreshed_at = NOW(), updated_at = NOW()
                            WHERE mattermost_user_id = %s
                            """,
                            (json.dumps(fresh_roles), mm_user_id),
                        )
                conn.commit()
        except Exception as exc:
            logger.warning(f"DB update failed after role refresh for user_id={mm_user_id!r}: {exc}")

        logger.info(f"Refreshed roles for user_id={mm_user_id!r}: {fresh_roles}")
        return fresh_roles
