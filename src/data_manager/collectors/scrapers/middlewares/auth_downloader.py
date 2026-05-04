"""
AuthDownloaderMiddleware — the single place where auth intersects Scrapy's
request/response lifecycle.

Everything else (spiders, parsers, pipelines) is auth-blind.

Middleware ordering (FR-3a — must be documented here per spec)
--------------------------------------------------------------
Request path (outbound):
    500  AuthDownloaderMiddleware   ← injects cookies/tokens FIRST
    550  RetryMiddleware            ← retries after credentials are attached
    600  RedirectMiddleware         ← follows 302s last

Response path (inbound — reversed order):
    600  RedirectMiddleware         ← resolves 302, re-queues new URL
    550  RetryMiddleware            ← handles transport errors
    500  AuthDownloaderMiddleware   ← sees the FINAL response (200 / 401 / 403)
                                      or catches the SSO poison-pill 200

Why auth before retry?
    If RetryMiddleware ran before auth on the *request* path, retried requests
    would carry no credentials and immediately receive another 401.  The retry
    counter exhausts before auth can refresh.  Placing auth at 500 ensures
    every outbound request carries valid credentials before retry even fires.

Why we do NOT handle 302 directly
    Scrapy's RedirectMiddleware (600) follows 302s before our middleware sees
    the response — we receive the final destination status.  *However*, CERN
    SSO signals session expiry with a silent  302 → /login → 200 OK  chain.
    The final 200 looks healthy but contains a login page.  We detect this
    in process_response via ``_is_login_redirect(response)``.

Required settings (settings.py)
--------------------------------
    DOWNLOADER_MIDDLEWARES = {
        "src.data_manager.collectors.scrapers.middlewares.AuthDownloaderMiddleware": 500,
        "scrapy.downloadermiddlewares.retry.RetryMiddleware": 550,
        # RedirectMiddleware stays at its default 600
    }

    SPIDER_AUTH_PROVIDERS = {
        "cern_sso": {
            "class": "src.data_manager.collectors.scrapers.auth.cern_sso.CERNSSOProvider",
            "kwargs": {"headless": True},
        },
        "indico": {
            "class": "src.data_manager.collectors.scrapers.auth.indico_bearer.IndicoBearerAuthProvider",
            "kwargs": {},
        },
    }

    AUTH_FAILURE_CODES = [401, 403]   # optional; this is the default

Spider contract
---------------
A spider opts into auth by declaring:

    auth_provider_name = "cern_sso"   # matches a key in SPIDER_AUTH_PROVIDERS

Spiders without this attribute are public and completely bypass this middleware.
"""
from __future__ import annotations

import importlib
from typing import Dict, Optional, TYPE_CHECKING

from scrapy import signals
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response
from twisted.internet.threads import deferToThread

from src.utils.logging import get_logger
from src.data_manager.collectors.scrapers.auth.base import AuthProvider, Credentials

if TYPE_CHECKING:
    from scrapy import Spider
    from scrapy.crawler import Crawler

logger = get_logger(__name__)

# Meta key that marks a request as a post-refresh retry.
# Prevents infinite refresh loops: if a retried request also fails auth,
# the middleware closes the spider instead of refreshing again.
_AUTH_RETRY_META_KEY = "_auth_retry"


class AuthDownloaderMiddleware:
    """Injects auth credentials and handles mid-crawl session expiry.

    Auth-provider-agnostic: resolves which provider to use from
    ``spider.auth_provider_name`` + ``settings.SPIDER_AUTH_PROVIDERS``.
    """

    def __init__(
        self,
        auth_providers_config: Dict,
        auth_failure_codes: list,
    ) -> None:
        self._config = auth_providers_config
        self._failure_codes = set(auth_failure_codes)
        # Keyed by provider name.  Populated lazily on first use.
        self._providers: Dict[str, AuthProvider] = {}
        self._credentials: Dict[str, Optional[Credentials]] = {}

    # ------------------------------------------------------------------
    # Scrapy classmethod + signal wiring
    # ------------------------------------------------------------------

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "AuthDownloaderMiddleware":
        mw = cls(
            auth_providers_config=crawler.settings.getdict(
                "SPIDER_AUTH_PROVIDERS", {}
            ),
            auth_failure_codes=crawler.settings.getlist(
                "AUTH_FAILURE_CODES", [401, 403]
            ),
        )
        crawler.signals.connect(mw._on_spider_closed, signal=signals.spider_closed)
        return mw

    def _on_spider_closed(self, spider: "Spider", reason: str) -> None:
        for name, provider in self._providers.items():
            try:
                provider.close()
                logger.debug("AuthMiddleware: closed provider %r", name)
            except Exception as exc:
                logger.warning(
                    "AuthMiddleware: error closing provider %r: %s", name, exc
                )

    # ------------------------------------------------------------------
    # process_request — inject credentials before the request is sent
    # ------------------------------------------------------------------

    def process_request(self, request: Request, spider: "Spider") -> None:
        """Inject credentials.  No-op for spiders without auth_provider_name."""
        provider_name = getattr(spider, "auth_provider_name", None)
        if not provider_name:
            return

        # Cache hit — no thread needed, inject inline
        cached = self._credentials.get(provider_name)
        if cached is not None and cached.is_valid():
            _inject(request, cached)
            return None

        # Cold start or stale — acquire blocks (Playwright), run in thread pool
        return deferToThread(self._blocking_acquire_and_inject, request, provider_name, spider)


    def _blocking_acquire_and_inject(self, request: Request, provider_name: str, spider: "Spider") -> None:
        """Runs in a thread — safe for sync Playwright."""
        creds = self._get_valid_credentials(provider_name, request.url, spider)
        if creds is None:
            logger.error(
                "AuthMiddleware: could not acquire credentials for %r — "
                "closing spider.", provider_name
            )
            self._close_spider(spider, "auth_acquisition_failed")
            raise IgnoreRequest("Auth acquisition failed -- no credentials found")

        _inject(request, creds)

    # ------------------------------------------------------------------
    # process_response — detect auth failure, refresh once, then close
    # ------------------------------------------------------------------

    def process_response(
        self, request: Request, response: Response, spider: "Spider"
    ) -> Response | Request:
        """Detect 401/403 and SSO login-redirect poison pill."""
        provider_name = getattr(spider, "auth_provider_name", None)
        if not provider_name:
            return response

        provider = self._resolve_provider(provider_name)
        failure_reason = self._detect_auth_failure(response, provider) if provider else None
        if failure_reason is None:
            return response  # healthy response — pass through

        if request.meta.get(_AUTH_RETRY_META_KEY):
            # Already refreshed once.  A second failure means the session is
            # broken beyond repair; do not retry again.
            logger.error(
                "AuthMiddleware: auth failure persists after refresh "
                "(%s, url=%s). Closing spider.",
                failure_reason,
                request.url,
            )
            self._close_spider(spider, "auth_expired")
            return response

        logger.warning(
            "AuthMiddleware: %s detected for %s — refreshing credentials.",
            failure_reason,
            request.url,
        )

        # Invalidate the cached credentials so _get_valid_credentials knows
        # they're stale before the next process_request call.
        cached = self._credentials.get(provider_name)
        if cached:
            cached.invalidate()

        # refresh also runs Playwright — thread pool
        return deferToThread(self._blocking_refresh_and_retry, request, provider_name, spider, failure_reason)

    def _blocking_refresh_and_retry(self, request: Request, provider_name: str, spider: "Spider", failure_reason: str) -> Request:
        """Runs in a thread — safe for sync Playwright."""
        fresh = self._do_refresh(provider_name, request.url, spider)
        if fresh is None:
            self._close_spider(spider, "auth_expired")
            raise IgnoreRequest("auth refresh failed")
        retry = request.copy()
        retry.meta[_AUTH_RETRY_META_KEY] = True
        retry = retry.replace(dont_filter=True)
        _inject(retry, fresh)
        return retry

    # ------------------------------------------------------------------
    # process_exception — log transport errors; let RetryMiddleware handle
    # ------------------------------------------------------------------

    def process_exception(
        self, request: Request, exception: Exception, spider: "Spider"
    ) -> None:
        provider_name = getattr(spider, "auth_provider_name", None)
        if provider_name:
            logger.warning(
                "AuthMiddleware: transport error [provider=%r] %s — %s",
                provider_name,
                request.url,
                exception,
            )
        # Return None → other middlewares (RetryMiddleware) handle it.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_auth_failure(self, response, provider: AuthProvider):
        """Return a failure label or None if the response looks healthy.

        Checks two failure modes:
        1. Explicit HTTP auth codes (401, 403).
        2. SSO poison-pill: a 200 OK whose final URL is a login page.
           CERN SSO sometimes redirects expired sessions to /login and returns
           a 200 with the login form HTML.  This is invisible to RetryMiddleware
           because the status code is 200 — only URL inspection reveals the trap.
        """
        if response.status in self._failure_codes:
            return f"HTTP {response.status}"
        if provider.is_session_expired(response):
            return "session-expired (provider-detected)"
        return None


    def _get_valid_credentials(
        self, provider_name: str, url: str, spider: "Spider"
    ) -> Optional[Credentials]:
        """Return cached credentials if still valid, or acquire fresh ones."""
        cached = self._credentials.get(provider_name)
        if cached is not None and cached.is_valid():
            return cached

        # Cache miss or explicitly invalidated / TTL expired — acquire fresh.
        logger.info(
            "AuthMiddleware: acquiring credentials via %r for %s",
            provider_name, url,
        )
        provider = self._resolve_provider(provider_name)
        if provider is None:
            return None

        fresh = provider.acquire(url)
        self._credentials[provider_name] = fresh
        return fresh

    def _do_refresh(
        self, provider_name: str, url: str, spider: "Spider"
    ) -> Optional[Credentials]:
        """Delegate refresh to the provider and update the cache."""
        provider = self._resolve_provider(provider_name)
        if provider is None:
            return None
        fresh = provider.refresh(url)
        self._credentials[provider_name] = fresh
        return fresh

    def _resolve_provider(self, name: str) -> Optional[AuthProvider]:
        """Return a cached provider, instantiating it on first call."""
        if name in self._providers:
            return self._providers[name]

        entry = self._config.get(name)
        if not entry:
            logger.error(
                "AuthMiddleware: no SPIDER_AUTH_PROVIDERS entry for %r. "
                "Check settings.py.", name
            )
            return None

        class_path: str = entry["class"]
        kwargs: dict = entry.get("kwargs", {})
        try:
            module_path, class_name = class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            provider_cls = getattr(module, class_name)
            provider: AuthProvider = provider_cls(**kwargs)
        except Exception as exc:
            logger.error(
                "AuthMiddleware: could not instantiate %r: %s",
                class_path, exc, exc_info=True,
            )
            return None

        self._providers[name] = provider
        return provider

    @staticmethod
    def _close_spider(spider: "Spider", reason: str) -> None:
        logger.error("AuthMiddleware: closing spider (reason=%r)", reason)
        try:
            spider.crawler.engine.close_spider(spider, reason)
        except Exception as exc:
            logger.error("AuthMiddleware: engine.close_spider failed: %s", exc)


# ---------------------------------------------------------------------------
# Standalone helper — lives outside the class so it's testable without
# constructing the full middleware.
# ---------------------------------------------------------------------------

def _inject(request: Request, credentials: Credentials) -> None:
    """Stamp cookies and/or auth headers onto a Scrapy Request in-place.

    Scrapy's Request.cookies accepts a list[dict] (same format Playwright's
    context.cookies() returns) or a plain dict.  We always normalise to
    list[dict] and merge rather than replace, so existing cookies (e.g. from
    a previous inject or a spider-level cookies= argument) are preserved.

    Headers are set directly on request.headers which is mutable.

    Note: Request.cookies is read-only after construction; we use
    request.replace(cookies=...) to produce a new Request object, then
    update the reference via the caller.  But since Scrapy passes Request
    objects by reference and the middleware hooks return None (pass-through)
    or a new Request, we instead mutate headers (mutable) and re-build the
    cookie jar using the internal _cookies attribute that Scrapy exposes.
    This is the idiomatic approach used by Scrapy's own cookie middleware.
    """
    if credentials.cookies:
        cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in credentials.cookies
        )
        request.headers["Cookie"] = cookie_header
        request.meta["dont_merge_cookies"] = True 
        # # Merge new cookies over existing ones (last write wins per name).
        # existing: list = list(request.cookies) if isinstance(request.cookies, list) else [
        #     {"name": k, "value": v} for k, v in (request.cookies or {}).items()
        # ]
        # merged: Dict[str, dict] = {c["name"]: c for c in existing}
        # for cookie in credentials.cookies:
        #     merged[cookie["name"]] = cookie
        # Replace is safe here — process_request returns None so Scrapy uses
        # the same object; we mutate via internal attribute.
        # request._cookies = list(merged.values())

    if credentials.headers:
        for key, value in credentials.headers.items():
            request.headers[key] = value