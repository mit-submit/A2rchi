"""
CERN SSO auth provider — Playwright implementation.

Why Playwright over Selenium (legacy SSOScraper used Selenium)
--------------------------------------------------------------
The legacy SSOScraper mixed browser lifecycle, cookie collection, crawling, and
link extraction into one class.  Now that auth is a pure credential factory
(Boundary B from the spec), the browser only needs to log in and hand back
cookies — Playwright's sync API is less boilerplate for this narrow use case:

    - No geckodriver binary management (Playwright installs its own browsers)
    - BrowserContext.cookies() returns the exact dict format Scrapy expects
    - context.clear_cookies() + re-login is cheaper than quitting/restarting
      a WebDriver session — critical for mid-crawl refresh without stalling the
      Twisted reactor for a long time
    - storage_state() lets us persist/restore auth state across restarts if we
      ever want that (Phase 2 enhancement)

Design
------
One ``Browser`` instance lives for the lifetime of the crawl (created lazily).
Each acquire/refresh operates on a fresh ``BrowserContext`` so sessions never
bleed between attempts.  The old context is closed before opening a new one.

Invalidation
------------
The middleware calls ``credentials.invalidate()`` and then ``provider.refresh()``
when it detects a 401, 403, or a login-page redirect.  ``refresh()`` here does:

    1. close the existing BrowserContext (clearing all cookies server-side too)
    2. open a new BrowserContext
    3. navigate to the target URL (which triggers the SSO redirect)
    4. fill in credentials and submit
    5. return a fresh Credentials object

The Browser process itself is NOT restarted on refresh — only the context,
which is a lightweight operation (~200ms vs ~2s for a full browser restart).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from urllib.parse import urlparse
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from src.utils.env import read_secret
from src.utils.logging import get_logger
from .base import AuthProvider, Credentials

logger = get_logger(__name__)

# Keycloak login form element IDs (CERN SSO uses standard Keycloak)
_USERNAME_SELECTOR = "#username"
_PASSWORD_SELECTOR = "#password"
_SUBMIT_SELECTOR   = "#kc-login"
_LOGIN_TIMEOUT_MS  = 20_000   # ms — Playwright uses milliseconds

# URL patterns that indicate we landed on a login page instead of content.
# Used by the middleware to detect the SSO poison-pill (302 → /login → 200 OK).
LOGIN_URL_PATTERNS: List[str] = [
    r"auth\.cern\.ch",
    r"/login",
    r"/sso/",
    r"keycloak",
]
_LOGIN_RE = re.compile("|".join(LOGIN_URL_PATTERNS), re.IGNORECASE)


def looks_like_login_page(url: str) -> bool:
    """Return True if *url* matches known CERN SSO login page patterns.

    Exported so the middleware can call it from process_response without
    importing the whole provider.
    """
    return bool(_LOGIN_RE.search(url))


class CERNSSOProvider(AuthProvider):
    """Acquires CERN SSO session cookies via a headless Playwright browser.

    Args:
        username:     CERN SSO username.  Falls back to SSO_USERNAME secret.
        password:     CERN SSO password.  Falls back to SSO_PASSWORD secret.
        headless:     Run browser headlessly (default True).
        browser_type: 'chromium' | 'firefox' | 'webkit'  (default 'chromium').
                      Chromium is faster for headless cookie extraction.
        slow_mo_ms:   Playwright slow-motion delay in ms.  0 in production,
                      useful for debugging (e.g. 500).
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = True,
        browser_type: str = "chromium",
        slow_mo_ms: int = 0,
    ) -> None:
        self.username: str = username or read_secret("SSO_USERNAME") or ""
        self.password: str = password or read_secret("SSO_PASSWORD") or ""
        self.headless = headless
        self.browser_type = browser_type
        self.slow_mo_ms = slow_mo_ms

        if not self.username or not self.password:
            raise ValueError(
                "CERNSSOProvider requires SSO_USERNAME and SSO_PASSWORD. "
                "Set them as secrets or pass them explicitly."
            )

        # Lazily initialised — browser starts only when acquire() is first called.
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

        logger.info(
            "CERNSSOProvider ready (browser=%s, headless=%s)",
            browser_type,
            headless,
        )

    # ------------------------------------------------------------------
    # AuthProvider contract
    # ------------------------------------------------------------------

    def acquire(self, url: str) -> Optional[Credentials]:
        """Full CERN SSO login flow.  Returns cookies as Credentials."""
        self._ensure_browser()
        self._open_fresh_context()
        return self._login_and_extract(url)

    def refresh(self, url: str) -> Optional[Credentials]:
        """Refresh by wiping the existing context and re-logging in.

        Reuses the running Browser process — only the BrowserContext is
        discarded, which is fast (~200 ms) and avoids stalling the Twisted
        reactor for a full browser restart.
        """
        logger.info("CERNSSOProvider: refreshing session for %s", url)
        self._close_context()          # wipe cookies server-side
        self._open_fresh_context()     # blank slate
        return self._login_and_extract(url)
    
    def is_session_expired(self, response) -> bool:
        return looks_like_login_page(response.url)

    def close(self) -> None:
        """Quit the browser process.  Called by middleware on spider_closed."""
        self._close_context()
        if self._browser:
            try:
                self._browser.close()
            except Exception as exc:
                logger.debug("CERNSSOProvider: browser.close() raised: %s", exc)
            finally:
                self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as exc:
                logger.debug("CERNSSOProvider: playwright.stop() raised: %s", exc)
            finally:
                self._playwright = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_browser(self) -> None:
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is None:
            launcher = getattr(self._playwright, self.browser_type)
            self._browser = launcher.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
            )
            logger.info(
                "CERNSSOProvider: %s browser started (headless=%s)",
                self.browser_type,
                self.headless,
            )

    def _open_fresh_context(self) -> None:
        """Close any existing context and open a blank new one."""
        self._close_context()
        assert self._browser is not None
        self._context = self._browser.new_context(
            # Accept cookies from any domain so SSO redirects set cookies freely.
            ignore_https_errors=True,
        )

    def _close_context(self) -> None:
        if self._context:
            try:
                self._context.close()
            except Exception as exc:
                logger.debug("CERNSSOProvider: context.close() raised: %s", exc)
            finally:
                self._context = None

    def _login_and_extract(self, url: str) -> Optional[Credentials]:
        """Navigate to *url*, complete SSO login, return Credentials."""
        assert self._context is not None
        page: Page = self._context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=30_000)

            # Public page: loaded directly without SSO redirect — return whatever
            # cookies the browser has (may be empty, that's fine for public pages).
            if not looks_like_login_page(page.url):
                # Try the site root — some sites like Discourse only redirect on the homepage
                origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
                page.goto(origin, wait_until="networkidle", timeout=30_000)
                if not looks_like_login_page(page.url):
                    # Still no SSO redirect — return whatever cookies we have
                    raw_cookies = self._context.cookies()
                    logger.info("CERNSSOProvider: no SSO redirect for %s, returning browser cookies", url)
                    return Credentials(cookies=raw_cookies)

            if not self._fill_login_form(page):
                return None

            # After submit, wait for navigation away from the login page.
            page.wait_for_url(
                lambda u: not looks_like_login_page(u),
                timeout=_LOGIN_TIMEOUT_MS,
            )

            # Navigate back to the original URL so all domain cookies are set.
            page.goto(url, wait_until="networkidle", timeout=30_000)

            raw_cookies: List[Dict] = self._context.cookies()
            logger.debug(
                "CERNSSOProvider: acquired %d cookies for %s",
                len(raw_cookies),
                url,
            )
            return Credentials(cookies=raw_cookies)

        except Exception as exc:
            logger.error(
                "CERNSSOProvider: login flow failed for %s: %s",
                url,
                exc,
                exc_info=True,
            )
            return None
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _fill_login_form(self, page: Page) -> bool:
        """Fill in and submit the Keycloak login form.

        Returns True if the submit was reached without timeout.
        """
        try:
            page.wait_for_selector(_USERNAME_SELECTOR, timeout=_LOGIN_TIMEOUT_MS)
            page.fill(_USERNAME_SELECTOR, self.username)
            page.fill(_PASSWORD_SELECTOR, self.password)
            page.click(_SUBMIT_SELECTOR)
            logger.info("CERNSSOProvider: login form submitted")
            return True
        except Exception as exc:
            logger.error(
                "CERNSSOProvider: could not find/fill login form: %s", exc
            )
            return False