"""
Mattermost Auth Manager - Maps Mattermost user identity to RBAC roles.

Supports two token_store modes:
  config  — static username→roles mapping in the config file (no DB, no SSO)
  db      — SSO-backed tokens stored in mattermost_tokens PostgreSQL table

Config structure (services.mattermost.auth):
    enabled: true
    token_store: db              # 'db' (SSO) or 'config' (static map)
    default_role: mattermost-restricted
    session_lifetime_days: 30
    roles_refresh_hours: 24
    login_base_url: "https://vocms248.cern.ch"
    sso:
      token_endpoint: "https://auth.cern.ch/auth/realms/cern/protocol/openid-connect/token"
    # Only used when token_store=config:
    user_roles:
      ahmedmu: [archi-admins]
"""

from typing import Dict, List, Optional

from src.utils.rbac.mattermost_context import MattermostUserContext
from src.utils.logging import get_logger

logger = get_logger(__name__)


class MattermostAuthManager:
    """
    Resolves Mattermost users to RBAC roles.

    In 'config' mode: static username/user_id → roles map from config.
    In 'db' mode: delegates to MattermostTokenService for SSO-backed roles.
                  Returns None when user has no stored token (triggers login prompt).
    """

    def __init__(self, auth_config: dict, pg_config: Optional[dict] = None):
        self.enabled: bool = auth_config.get('enabled', False)
        self.token_store: str = auth_config.get('token_store', 'config')
        self.default_role: str = auth_config.get('default_role', 'mattermost-restricted')
        self.login_base_url: str = auth_config.get('login_base_url', '').rstrip('/')
        self.user_roles: Dict[str, List[str]] = auth_config.get('user_roles', {})

        self._token_service = None
        if self.enabled and self.token_store == 'db':
            if pg_config:
                self._init_token_service(auth_config, pg_config)
            else:
                logger.warning(
                    "MattermostAuthManager: token_store=db but no pg_config provided. "
                    "Falling back to token_store=config."
                )
                self.token_store = 'config'

        if self.enabled:
            logger.info(
                f"MattermostAuthManager: enabled=True, token_store={self.token_store!r}, "
                f"default_role={self.default_role!r}"
            )
        else:
            logger.info(
                f"MattermostAuthManager: disabled — all users get "
                f"default_role={self.default_role!r}"
            )

    def _init_token_service(self, auth_config: dict, pg_config: dict) -> None:
        try:
            from src.utils.mattermost_token_service import MattermostTokenService
            sso_cfg = auth_config.get('sso', {})
            self._token_service = MattermostTokenService(
                pg_config=pg_config,
                token_endpoint=sso_cfg.get('token_endpoint', ''),
                session_lifetime_days=int(auth_config.get('session_lifetime_days', 30)),
                roles_refresh_hours=int(auth_config.get('roles_refresh_hours', 24)),
            )
            logger.info("MattermostAuthManager: DB token service initialized")
        except Exception as exc:
            logger.error(f"MattermostAuthManager: failed to init token service: {exc}")
            self._token_service = None
            self.token_store = 'config'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_context(
        self, user_id: str, username: str = "", email: str = ""
    ) -> Optional[MattermostUserContext]:
        """
        Return a MattermostUserContext for the given Mattermost user.

        Returns None in db mode when the user has no stored token — the caller
        should send a login link and abort processing.

        Returns a context with default_role in config mode for unknown users.
        """
        if not self.enabled:
            return MattermostUserContext(
                user_id=user_id, username=username,
                roles=[self.default_role], email=email,
            )

        if self.token_store == 'db' and self._token_service:
            return self._token_service.get_user_context(user_id, username)

        # config mode — static lookup
        roles = self._static_roles(username, user_id)
        return MattermostUserContext(
            user_id=user_id, username=username, roles=roles, email=email,
        )

    def login_url(self, user_id: str, username: str = "") -> str:
        """Build the SSO login URL to send to an unauthenticated Mattermost user."""
        base = self.login_base_url or "http://localhost:7861"
        url = f"{base}/mattermost-auth?state={user_id}"
        if username:
            url += f"&username={username}"
        return url

    def invalidate(self, user_id: str) -> None:
        """Force re-login for a user (admin action)."""
        if self._token_service:
            self._token_service.invalidate(user_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _static_roles(self, username: str, user_id: str) -> List[str]:
        """Config-mode role lookup: username first, then user_id, then default."""
        roles = self.user_roles.get(username) or self.user_roles.get(user_id)
        if roles:
            logger.debug(
                f"MattermostAuthManager: @{username!r} (id={user_id!r}) -> roles={roles}"
            )
            return roles
        logger.debug(
            f"MattermostAuthManager: unknown user @{username!r} (id={user_id!r}), "
            f"assigning default_role={self.default_role!r}"
        )
        return [self.default_role]
