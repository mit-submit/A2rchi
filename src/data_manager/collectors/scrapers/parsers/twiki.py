"""
TWiki / PatternSkin parser.

1. **PDF** — same rule as ``parse_link_page``: raw ``response.body``, ``suffix="pdf"``.
2. **HTML** — **outer HTML** of the main column (DOM subtree), not ``*::text``.

Selectors are tried in order; first non-empty serialized node wins, then ``body``.
"""
from __future__ import annotations

from typing import Iterator, List

from scrapy.http import Response, TextResponse

from src.data_manager.collectors.scrapers.items import WebPageItem
from src.data_manager.collectors.scrapers.utils import get_content_type
from src.utils.logging import get_logger
from urllib.parse import urlparse

logger = get_logger(__name__)

_TWIKI_DOM_SELECTORS: List[str] = [
    "body.patternViewPage #patternMainContents",
    "#patternMainContents",
    "body.patternViewPage #patternMain",
    "#patternMain",
    "#twikiMainContents",
    ".patternViewBody",
    ".twikiTopicText",
    ".patternTopic",
    ".patternContent",
    ".patternMain",
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


def _twiki_title(response: TextResponse) -> str:
    raw = (
        response.css("#topic-title::text").get()
        or response.css(".patternTitle::text").get()
        or response.css("title::text").get()
        or ""
    )
    if not isinstance(raw, str):
        return ""
    # CERN TWiki example: <title> CRAB3ConfigurationFile < CMSPublic < TWiki</title>
    return raw.split("<")[0].strip() 


def parse_twiki_page(response: Response) -> Iterator[WebPageItem]:
    ct = get_content_type(response)

    # ── PDF (aligned with parse_link_page) ─────────────────────────────────
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

    # ── HTML DOM ────────────────────────────────────────────────────────────
    if not isinstance(response, TextResponse):
        logger.debug("Skipping non-text response (no css): %s", response.url)
        return

    title = _twiki_title(response)
    body_html = _first_outer_html(response, _TWIKI_DOM_SELECTORS)
    if not body_html:
        logger.debug("No main-column HTML for Twiki page: %s", response.url)
        return

    yield WebPageItem(
        url=response.url,
        title=title,
        content=body_html,
        suffix="html",
        source_type="web",
        content_type=ct,
        encoding=response.encoding or "utf-8",
    )
