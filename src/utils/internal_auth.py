from __future__ import annotations

import hashlib
from typing import Dict, Tuple

from src.utils.env import read_secret


def resolve_data_manager_service_token() -> Tuple[str, str]:
    """Return the internal token used by chat-app -> data-manager requests.

    Prefer an explicit DM_API_TOKEN when provided. If it is absent, derive a
    deterministic internal-only token from PG_PASSWORD so paired services can
    still authenticate without extra configuration.
    """
    explicit_token = read_secret("DM_API_TOKEN")
    if explicit_token:
        return explicit_token, "DM_API_TOKEN"

    pg_password = read_secret("PG_PASSWORD")
    if not pg_password:
        return "", "missing"

    digest = hashlib.sha256()
    digest.update(b"archi:data-manager:")
    digest.update(pg_password.encode("utf-8"))
    return digest.hexdigest(), "derived-from-PG_PASSWORD"


def build_data_manager_auth_headers() -> Dict[str, str]:
    """Build auth headers for requests to the data-manager service."""
    token, _ = resolve_data_manager_service_token()
    return {"Authorization": f"Bearer {token}"} if token else {}
