"""
Mattermost User Context - Thread-safe per-request user context for Mattermost.

Provides a ContextVar-based mechanism to carry Mattermost user identity
through the call stack without needing Flask sessions.
"""

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MattermostUserContext:
    user_id: str
    username: str
    roles: List[str]
    email: str = ""


# Module-level ContextVar — default None means "no Mattermost context active"
_mm_context: ContextVar[Optional[MattermostUserContext]] = ContextVar(
    'mm_user_context', default=None
)


def get_mattermost_context() -> Optional[MattermostUserContext]:
    """Return the active Mattermost user context, or None if not set."""
    return _mm_context.get()


@contextmanager
def mattermost_user_context(ctx: MattermostUserContext):
    """
    Context manager that sets the Mattermost user context for the duration
    of the block, then resets it. Thread-safe via ContextVar.

    Usage:
        with mattermost_user_context(ctx):
            answer, _ = ai_wrapper(post)
    """
    token = _mm_context.set(ctx)
    try:
        yield ctx
    finally:
        _mm_context.reset(token)
