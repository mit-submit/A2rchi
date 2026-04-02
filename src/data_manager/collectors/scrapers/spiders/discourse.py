"""
Discourse spider — recursive JSON pagination, no link following.

Seed:   GET /c/{path}.json              → first page of each category
Recur:  GET more_topics_url (from JSON) → next page (until exhausted)
Fan-out: each topic → GET /t/{slug}/{id}.rss → yield WebPageItem
"""
from __future__ import annotations

import re
import json
from typing import Any, Iterator, List, Optional
from urllib.parse import urljoin

from scrapy import Spider
from scrapy.http import Request, Response, TextResponse

from src.data_manager.collectors.scrapers.items import DiscourseTopicPageItem
from src.utils.logging import get_logger

logger = get_logger(__name__)


class DiscourseSpider(Spider):
    name = "discourse"

    _DEFAULT_BASE_URL = "https://cms-talk.web.cern.ch"
    _DEFAULT_CATEGORY_PATHS: List[str] = [
        "/c/offcomp/comptools/87",
    ]

    auth_provider_name = "cern_sso"

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 60,
        "RETRY_TIMES": 2,
        "COOKIES_ENABLED": True,
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        category_paths: Optional[List[str]] = None,
        keywords: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = (base_url or self._DEFAULT_BASE_URL).rstrip("/")
        self.category_paths = category_paths or self._DEFAULT_CATEGORY_PATHS
        self.keywords_re = re.compile(keywords) if keywords else None

    # ── Seeds: one request per category (page 0) ────────────────────────
    async def start(self):
        for path in self.category_paths:
            path = path.strip("/")
            url = f"{self.base_url}/{path}.json"
            yield Request(
                url=url,
                callback=self.parse_category,
                errback=self.errback,
                meta={"category_path": path},
            )

    # ── Category JSON → topic RSS requests + next page ──────────────────
    def parse_category(self, response: Response) -> Iterator[Request]:
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.error("Failed to parse category JSON %s: %s", response.url, exc)
            return

        topic_list = data.get("topic_list", {})
        topics = topic_list.get("topics", []) or []
        category_path = response.meta.get("category_path", "?")
        logger.info(
            "Category %s returned %d topics (%s)",
            category_path, len(topics), response.url,
        )

        for topic in topics:
            slug = topic.get("slug", "")
            topic_id = topic.get("id")
            if not slug or not topic_id:
                continue
            rss_url = f"{self.base_url}/t/{slug}/{topic_id}.rss"
            yield Request(
                url=rss_url,
                callback=self.parse_topic,
                errback=self.errback,
                meta={
                    "topic_id": topic_id,
                    "slug": slug,
                    "title": topic.get("title", f"{slug} ({topic_id})"),
                    "tags": topic.get("tags", []),
                    "has_accepted_answer": topic.get("has_accepted_answer", False),
                    "created_at": topic.get("created_at", ""),
                },
            )

        # Recurse: follow more_topics_url if present
        more_url = topic_list.get("more_topics_url")
        if more_url:
            next_url = urljoin(response.url, more_url)
            if ".json" not in next_url:
                # Insert .json before the query string:
                # /c/.../87?page=1 → /c/.../87.json?page=1
                if "?" in next_url:
                    path, qs = next_url.split("?", 1)
                    next_url = f"{path}.json?{qs}"
                else:
                    next_url += ".json"
            yield Request(
                url=next_url,
                callback=self.parse_category,
                errback=self.errback,
                meta={"category_path": category_path},
            )
        else:
            logger.info("Category %s exhausted (no more_topics_url)", category_path)

    def _content_matches_keywords(self, text: str) -> bool:
        """No keywords pattern means accept everything."""
        if self.keywords_re is None:
            return True
        return bool(self.keywords_re.search(text))

    # ── Topic RSS → DiscourseTopicPageItem ───────────────────────────────
    def parse_topic(self, response: Response) -> Iterator[DiscourseTopicPageItem]:
        if not isinstance(response, TextResponse):
            logger.debug("Skipping non-text response: %s", response.url)
            return

        if not self._content_matches_keywords(response.text):
            logger.debug("Skipping topic (no keyword match): %s", response.url)
            return

        slug = response.meta.get("slug", "")
        topic_id = response.meta.get("topic_id", "")
        title = response.meta.get("title", "")
        tags = response.meta.get("tags", [])

        yield DiscourseTopicPageItem(
            url=response.url,
            content=response.text,
            suffix="rss",
            source_type="web",
            title=title,
            content_type=response.headers.get("Content-Type", b"").decode(
                "utf-8", errors="replace"
            ),
            encoding=response.encoding or "utf-8",
            topic_id=topic_id,
            slug=slug,
            tags=tags,
            has_accepted_answer=response.meta.get("has_accepted_answer", False),
            created_at=response.meta.get("created_at", ""),
        )

    def errback(self, failure):
        self.logger.error(
            "Request failed: %s — %s",
            failure.request.url,
            repr(failure.value),
        )