"""
Base auth contract: Credentials value object + AuthProvider ABC.

Scrapy SoC note
---------------
Providers are *credential factories only*.  They know how to acquire,
validate, and refresh credentials.  They have zero knowledge of Scrapy
Requests, Responses, spiders, or pipelines.  The middleware decides *when*
to call the provider; the provider decides *how* to produce valid credentials.

Credential lifecycle (owned by AuthDownloaderMiddleware):
    1. acquire(url)   — full login flow, called lazily on the first request
    2. inject         — middleware stamps cookies/headers onto the Request
    3. is_valid()     — middleware may pre-check before each request (optional)
    4. refresh(url)   — called on 401/403 or detected login-redirect
    5. invalidate()   — marks credentials stale; next request triggers refresh
    6. close()        — release browser/driver resources on spider_closed signal
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Credentials:
    """Immutable value object carrying whatever the downloader needs.

    Either ``cookies`` (session-based SSO) or ``headers`` (bearer token) or both.
    Never mutated after creation — callers call provider.refresh() to get a new one.

    ``acquired_at`` and ``ttl_seconds`` are optional hints.  If the provider
    knows the session lifetime (e.g. from a Set-Cookie Max-Age), it sets them
    so the middleware can pre-emptively refresh before a request fails rather
    than waiting for a 401.

    ``_valid`` is an internal flag; use invalidate() / is_valid() rather than
    touching it directly.
    """

    cookies: List[Dict] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    acquired_at: float = field(default_factory=time.monotonic)
    ttl_seconds: Optional[float] = None   # None = unknown / infinite
    _valid: bool = field(default=True, repr=False, init=False)

    def is_empty(self) -> bool:
        return not self.cookies and not self.headers

    def is_valid(self) -> bool:
        """Return False if explicitly invalidated or if TTL has elapsed."""
        if not self._valid:
            return False
        if self.ttl_seconds is not None:
            return (time.monotonic() - self.acquired_at) < self.ttl_seconds
        return True

    def invalidate(self) -> None:
        """Mark these credentials as stale.  Thread-safe for single-threaded Twisted."""
        self._valid = False


class AuthProvider(ABC):
    """Abstract base for all auth providers.

    Instantiated once per crawl inside AuthDownloaderMiddleware.from_crawler()
    so providers can be swapped for test fakes without touching any spider.

    Concrete implementations must be importable via their dotted class path
    registered in settings.SPIDER_AUTH_PROVIDERS.
    """

    @abstractmethod
    def acquire(self, url: str) -> Optional[Credentials]:
        """Full authentication flow.  Returns Credentials or None on failure."""

    def refresh(self, url: str) -> Optional[Credentials]:
        """Re-authenticate.  Default: delegates to acquire().

        Override for providers that have a cheaper refresh path (e.g. a
        /token/refresh endpoint that doesn't need a full browser login).
        """
        return self.acquire(url)
    
    def is_session_expired(self, response) -> bool:
        """Return True if response indicates session expiry.
        Default checks only explicit HTTP auth codes via the middleware's
        failure_codes list.  Override for providers whose SSO signals
        expiry via a 302→200 poison-pill (CERN) or a JSON error body (APIs).
        """
        return False

    def close(self) -> None:
        """Release resources (browser context, HTTP session, etc.)."""