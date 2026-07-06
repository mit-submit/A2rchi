"""
MCPOAuthService - OAuth2 authorization for MCP servers (MCP 2025-11 spec).

Implements the full authorization code + PKCE flow with dynamic client registration:
  1. Discover auth server via RFC 9728 /.well-known/oauth-protected-resource
  2. Fetch server metadata via RFC 8414 /.well-known/oauth-authorization-server
  3. Register archi as a client via RFC 7591 dynamic registration (once per server)
  4. User authorizes via /mcp/authorize → MCP server → /mcp/callback
  5. Exchange auth code for MCP-issued access + refresh tokens
  6. Store per-user per-server tokens encrypted in PostgreSQL
  7. Silently refresh tokens when expired
"""

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import urlencode, urlparse

import requests as http_requests

from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

_CA_BUNDLE = "/etc/ssl/certs/tls-ca-bundle.pem"
_VERIFY = _CA_BUNDLE if os.path.exists(_CA_BUNDLE) else True


class MCPOAuthService:
    """
    Manages the OAuth2 authorization code + PKCE flow for MCP servers
    that implement their own authorization server (MCP 2025-11 spec).
    """

    def __init__(self, pg_config: dict = None, app_base_url: str = ""):
        self.pg_config = pg_config or {}
        self.app_base_url = app_base_url.rstrip("/")
        self._encryption_key = (
            read_secret("BYOK_ENCRYPTION_KEY")
            or read_secret("PG_ENCRYPTION_KEY")
            or read_secret("UPLOADER_SALT")
            or read_secret("FLASK_UPLOADER_APP_SECRET_KEY")
        )
        if not self._encryption_key:
            logger.warning("MCPOAuthService: no encryption key found — tokens will not be persisted")

    # ------------------------------------------------------------------
    # Discovery & Registration
    # ------------------------------------------------------------------

    def discover_auth_server(self, server_url: str) -> Optional[dict]:
        """
        Discover the OAuth2 authorization server metadata for an MCP server URL.
        Uses RFC 9728 /.well-known/oauth-protected-resource then RFC 8414.
        """
        parsed = urlparse(server_url)
        host_url = f"{parsed.scheme}://{parsed.netloc}"

        try:
            resp = http_requests.get(
                f"{host_url}/.well-known/oauth-protected-resource",
                verify=_VERIFY, timeout=10,
            )
            if resp.status_code != 200:
                logger.debug(f"No protected-resource metadata at {host_url}: {resp.status_code}")
                return None
            resource_meta = resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch protected-resource metadata for {server_url}: {e}")
            return None

        auth_server_urls = resource_meta.get("authorization_servers", [])
        if not auth_server_urls:
            return None

        auth_base = auth_server_urls[0].rstrip("/")
        for path in ["/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"]:
            try:
                resp = http_requests.get(f"{auth_base}{path}", verify=_VERIFY, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                logger.debug(f"Failed fetching {auth_base}{path}: {e}")

        return None

    def get_or_register_client(self, server_name: str, server_url: str) -> Optional[dict]:
        """
        Return existing client registration or perform dynamic registration (RFC 7591).
        Returns dict with client_id, client_secret, redirect_uri, auth_meta.
        """
        existing = self._fetch_client_registration(server_name)
        if existing:
            return existing

        auth_meta = self.discover_auth_server(server_url)
        if not auth_meta:
            logger.warning(f"Could not discover auth server for MCP server '{server_name}'")
            return None

        registration_endpoint = auth_meta.get("registration_endpoint")
        if not registration_endpoint:
            logger.warning(f"MCP server '{server_name}' has no registration_endpoint")
            return None

        redirect_uri = f"{self.app_base_url}/mcp/callback"
        try:
            resp = http_requests.post(
                registration_endpoint,
                json={
                    "client_name": "archi",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
                verify=_VERIFY, timeout=10,
            )
            resp.raise_for_status()
            reg = resp.json()
        except Exception as e:
            logger.error(f"Client registration failed for MCP server '{server_name}': {e}")
            return None

        client_id = reg.get("client_id")
        if not client_id:
            logger.error(f"No client_id in registration response for '{server_name}'")
            return None

        client_secret = reg.get("client_secret", "")
        self._store_client_registration(server_name, server_url, client_id, client_secret,
                                         redirect_uri, auth_meta)
        logger.info(f"Registered OAuth2 client for MCP server '{server_name}': {client_id!r}")
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "auth_meta": auth_meta,
        }

    # ------------------------------------------------------------------
    # Authorization URL + PKCE
    # ------------------------------------------------------------------

    def get_authorization_url(
        self, server_name: str, server_url: str
    ) -> Optional[Tuple[str, str, str]]:
        """
        Build the authorization redirect URL.
        Returns (authorization_url, state, code_verifier) or None.
        """
        reg = self.get_or_register_client(server_name, server_url)
        if not reg:
            return None

        auth_endpoint = reg["auth_meta"].get("authorization_endpoint")
        if not auth_endpoint:
            return None

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": reg["client_id"],
            "redirect_uri": reg["redirect_uri"],
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{auth_endpoint}?{urlencode(params)}"
        return auth_url, state, code_verifier

    # ------------------------------------------------------------------
    # Token Exchange & Refresh
    # ------------------------------------------------------------------

    def exchange_code(self, server_name: str, code: str, code_verifier: str) -> Optional[dict]:
        """Exchange an authorization code for tokens."""
        reg = self._fetch_client_registration(server_name)
        if not reg:
            return None

        token_endpoint = reg["auth_meta"].get("token_endpoint")
        if not token_endpoint:
            return None

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": reg["redirect_uri"],
            "client_id": reg["client_id"],
            "code_verifier": code_verifier,
        }
        if reg.get("client_secret"):
            data["client_secret"] = reg["client_secret"]

        try:
            resp = http_requests.post(token_endpoint, data=data, verify=_VERIFY, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Token exchange failed for MCP server '{server_name}': {e}")
            return None

    def _refresh_access_token(self, server_name: str, user_id: str,
                               refresh_token: str) -> Optional[str]:
        reg = self._fetch_client_registration(server_name)
        if not reg:
            return None

        token_endpoint = reg["auth_meta"].get("token_endpoint")
        if not token_endpoint or not refresh_token:
            return None

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": reg["client_id"],
        }
        if reg.get("client_secret"):
            data["client_secret"] = reg["client_secret"]

        try:
            resp = http_requests.post(token_endpoint, data=data, verify=_VERIFY, timeout=10)
            resp.raise_for_status()
            token_data = resp.json()
        except Exception as e:
            logger.warning(f"Token refresh failed for '{server_name}', user={user_id!r}: {e}")
            return None

        new_access = token_data.get("access_token")
        new_refresh = token_data.get("refresh_token") or refresh_token
        expires_in = int(token_data.get("expires_in", 3600))
        if new_access:
            self.store_user_token(user_id, server_name, new_access, new_refresh, expires_in)
        return new_access

    # ------------------------------------------------------------------
    # User token storage
    # ------------------------------------------------------------------

    def store_user_token(self, user_id: str, server_name: str, access_token: str,
                          refresh_token: Optional[str], expires_in: int = 3600) -> None:
        if not self._encryption_key:
            return

        now = datetime.now(timezone.utc)
        access_expires_at = now + timedelta(seconds=expires_in)
        session_expires_at = now + timedelta(days=30)

        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO mcp_oauth_tokens
                            (user_id, server_name, access_token, refresh_token,
                             access_token_expires_at, session_expires_at, updated_at)
                        VALUES (%s, %s,
                                pgp_sym_encrypt(%s, %s),
                                pgp_sym_encrypt(%s, %s),
                                %s, %s, NOW())
                        ON CONFLICT (user_id, server_name) DO UPDATE SET
                            access_token            = EXCLUDED.access_token,
                            refresh_token           = EXCLUDED.refresh_token,
                            access_token_expires_at = EXCLUDED.access_token_expires_at,
                            session_expires_at      = EXCLUDED.session_expires_at,
                            updated_at              = NOW()
                        """,
                        (
                            user_id, server_name,
                            access_token, self._encryption_key,
                            refresh_token or "", self._encryption_key,
                            access_expires_at, session_expires_at,
                        ),
                    )
                conn.commit()
            logger.info(f"Stored MCP token for user={user_id!r}, server={server_name!r}, "
                        f"expires={access_expires_at.isoformat()}")
        except Exception as e:
            logger.error(f"Failed to store MCP token for user={user_id!r}, server={server_name!r}: {e}")

    def get_access_token(self, user_id: str, server_name: str) -> Optional[str]:
        """Return a valid access token, silently refreshing if expired."""
        if not user_id or not self._encryption_key:
            return None

        row = self._fetch_user_token(user_id, server_name)
        if row is None:
            return None

        access_token, refresh_token, access_expires_at, session_expires_at = row
        now = datetime.now(timezone.utc)

        if session_expires_at and now > session_expires_at:
            self.invalidate_user_token(user_id, server_name)
            return None

        if access_expires_at and now < access_expires_at:
            return access_token

        logger.info(f"MCP access token expired for user={user_id!r}, server={server_name!r}, refreshing")
        return self._refresh_access_token(server_name, user_id, refresh_token)

    def invalidate_user_token(self, user_id: str, server_name: str) -> None:
        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM mcp_oauth_tokens WHERE user_id = %s AND server_name = %s",
                        (user_id, server_name),
                    )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to invalidate MCP token for user={user_id!r}, server={server_name!r}: {e}")

    def get_servers_needing_auth(self, user_id: str, mcp_servers: dict) -> list:
        """Return list of server names that require OAuth but have no valid token."""
        return [
            name for name, cfg in mcp_servers.items()
            if cfg.get("sso_auth") and not self.get_access_token(user_id, name)
        ]

    # ------------------------------------------------------------------
    # Internal DB helpers
    # ------------------------------------------------------------------

    def _store_client_registration(self, server_name: str, server_url: str,
                                    client_id: str, client_secret: str,
                                    redirect_uri: str, auth_meta: dict) -> None:
        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO mcp_oauth_clients
                            (server_name, server_url, client_id, client_secret,
                             redirect_uri, auth_meta)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (server_name) DO UPDATE SET
                            server_url    = EXCLUDED.server_url,
                            client_id     = EXCLUDED.client_id,
                            client_secret = EXCLUDED.client_secret,
                            redirect_uri  = EXCLUDED.redirect_uri,
                            auth_meta     = EXCLUDED.auth_meta,
                            updated_at    = NOW()
                        """,
                        (server_name, server_url, client_id, client_secret,
                         redirect_uri, json.dumps(auth_meta)),
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to store client registration for '{server_name}': {e}")

    def _fetch_client_registration(self, server_name: str) -> Optional[dict]:
        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT client_id, client_secret, redirect_uri, auth_meta "
                        "FROM mcp_oauth_clients WHERE server_name = %s",
                        (server_name,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    client_id, client_secret, redirect_uri, auth_meta_raw = row
                    return {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": redirect_uri,
                        "auth_meta": (
                            json.loads(auth_meta_raw)
                            if isinstance(auth_meta_raw, str)
                            else auth_meta_raw
                        ),
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch client registration for '{server_name}': {e}")
            return None

    def _fetch_user_token(self, user_id: str, server_name: str):
        try:
            with self._get_pool().get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pgp_sym_decrypt(access_token,  %s)::text,
                               pgp_sym_decrypt(refresh_token, %s)::text,
                               access_token_expires_at,
                               session_expires_at
                        FROM mcp_oauth_tokens
                        WHERE user_id = %s AND server_name = %s
                        """,
                        (self._encryption_key, self._encryption_key, user_id, server_name),
                    )
                    return cur.fetchone()
        except Exception as e:
            logger.warning(f"Failed to fetch MCP token for user={user_id!r}, server={server_name!r}: {e}")
            return None

    def _get_pool(self):
        from src.utils.postgres_service_factory import PostgresServiceFactory
        factory = PostgresServiceFactory.get_instance()
        if factory:
            return factory.connection_pool
        from src.utils.connection_pool import ConnectionPool
        return ConnectionPool(connection_params=self.pg_config)
