from typing import Iterator, List
from scrapy.http import Response, TextResponse
from urllib.parse import urlparse
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

def _first_outer_html(response: Response, selectors: List[str]) -> str:
    for selector in selectors:
        nodes = response.css(selector)
        if not nodes:
            continue
        html = nodes[0].get()
        if html and html.strip():
            return html.strip()
    return ""

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
            title=urlparse(response.url).path.split("/")[-1].replace(".pdf", "").strip(),
            content_type=ct,
        )
        return
    # ── HTML ─────────────────────────────────────────────────────────────────
    title = (
        response.css("h1::text").get()
        or response.css("title::text").get()
        or ""
    ).strip()
    body_text = _first_outer_html(response, _CONTENT_SELECTORS)
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
