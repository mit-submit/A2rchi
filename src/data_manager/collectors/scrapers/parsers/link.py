from typing import Iterator
from scrapy.http import Response, TextResponse
from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type
# Tried in order — first non-empty match wins.
# Covers: HTML5 semantic, ARIA landmark, common CMS patterns, final fallback.
_CONTENT_SELECTORS = [
    "main",
    "article",
    '[role="main"]',
    "#content",
    "#main",
    "#main-content",
    ".main-content",          # MIT.edu Drupal wrapper
    ".region-content",        # Drupal generic region
    ".content",
    ".post-content",
    ".entry-content",
    "body",
]
def parse_link_page(response: Response) -> Iterator[WebPageItem]:
    """
    Generic page parser — works for any HTML page with no site-specific selectors.
    Strategy:
    - PDFs: return raw bytes, suffix="pdf".
    - HTML: extract visible text from the first matching content container,
      falling back through _CONTENT_SELECTORS to <body>.
      Full raw HTML is never stored — only visible text reaches the item.
    Suitable as the default parse_item for LinkSpider subclasses that have
    no meaningful site-specific structure to exploit.
    """
    ct = get_content_type(response)
    # ── PDF ──────────────────────────────────────────────────────────────────
    if response.url.lower().endswith(".pdf") or "application/pdf" in ct:
        yield WebPageItem(
            url=response.url,
            content=response.body,
            suffix="pdf",
            source_type="web",
            title="",
            content_type=ct,
        )
        return
    # ── HTML ─────────────────────────────────────────────────────────────────
    title = (
        response.css("h1::text").get()
        or response.css("title::text").get()
        or ""
    ).strip()
    body_text = _extract_main_text(response)
    encoding = response.encoding if isinstance(response, TextResponse) else "utf-8"
    if not body_text:
        return  # empty page — don't yield a blank item
    yield WebPageItem(
        url=response.url,
        content=body_text,
        suffix="html",
        source_type="web",
        title=title,
        content_type=ct,
        encoding=encoding,
    )
def _extract_main_text(response: Response) -> str:
    """
    Try content selectors in priority order.
    Returns the first non-empty joined text, or empty string.
    """
    for selector in _CONTENT_SELECTORS:
        text = " ".join(response.css(f"{selector} *::text").getall()).strip()
        if text:
            return text
    return ""