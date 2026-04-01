"""
ELOG scraper for electronic logbooks (https://elog.sourceforge.net/).

Automatically discovers all entries by walking index pages sequentially,
then fetches each individual entry as a ScrapedResource.

Config (under data_manager.sources.elog):
    url:          Base URL of the logbook, e.g. https://www-enstore.fnal.gov/elog/dCache/
    max_entries:  Optional cap on total entries to fetch (default: unlimited)
    verify_ssl:   Whether to verify SSL certificates (default: True)
"""

import re
import requests
from typing import Dict, Iterator, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource
from src.utils.logging import get_logger

logger = get_logger(__name__)

_ENTRY_PATH = re.compile(r"/\d+$")


class ElogScraper:
    """Crawls an ELOG logbook index (walking pages sequentially) and yields each entry."""

    def __init__(self, config: dict) -> None:
        self.base_url   = config.get("url", "").rstrip("/") + "/"
        self.max_entries: Optional[int] = config.get("max_entries")
        self.verify_ssl = config.get("verify_ssl", True)
        self._session   = requests.Session()
        if not self.verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def iter_entries(self) -> Iterator[ScrapedResource]:
        """Yield one ScrapedResource per logbook entry, newest first."""
        entry_urls = self._discover_entry_urls()
        fetched = 0
        for url in entry_urls:
            if self.max_entries is not None and fetched >= self.max_entries:
                logger.info(f"Reached max_entries={self.max_entries}; stopping.")
                break
            resource = self._fetch_entry(url)
            if resource is not None:
                yield resource
                fetched += 1
        logger.info(f"ElogScraper: fetched {fetched} entries from {self.base_url}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _discover_entry_urls(self) -> List[str]:
        """Return entry URLs newest-first by walking index pages sequentially until max_entries is reached."""
        seen:   Set[str]  = set()
        result: List[str] = []
        page = 1

        while True:
            page_url = self.base_url if page == 1 else f"{self.base_url}page{page}"
            new_urls = [u for u in self._get_entry_urls_from_page(page_url) if u not in seen]
            if not new_urls:
                logger.info(f"ElogScraper: no new entries on {page_url}, stopping.")
                break
            seen.update(new_urls)
            result.extend(new_urls)
            logger.debug(f"ElogScraper: page {page} added {len(new_urls)} entries ({len(result)} total)")
            if self.max_entries is not None and len(result) >= self.max_entries:
                break
            page += 1

        result.sort(key=lambda u: int(u.rstrip("/").rsplit("/", 1)[-1]), reverse=True)
        logger.info(f"ElogScraper: discovered {len(result)} unique entries")
        return result

    def _get_entry_urls_from_page(self, page_url: str) -> List[str]:
        """Return all entry URLs found on a single index/listing page."""
        html = self._fetch_html(page_url)
        if html is None:
            return []
        soup  = BeautifulSoup(html, "html.parser")
        base_host = urlparse(self.base_url).netloc
        entries: Set[str] = set()
        for a in soup.find_all("a", href=True):
            full = urljoin(page_url, a["href"])
            parsed = urlparse(full)
            if parsed.netloc == base_host and _ENTRY_PATH.search(parsed.path):
                # Strip query/fragment so we get the canonical entry URL
                entries.add(parsed._replace(query="", fragment="").geturl())
        return list(entries)

    def _fetch_entry(self, url: str) -> Optional[ScrapedResource]:
        """Fetch a single entry page, extract structured text, and return a ScrapedResource."""
        html = self._fetch_html(url)
        if html is None:
            return None
        text, metadata = self._parse_entry(html, url)
        return ScrapedResource(
            url=url,
            content=text,
            suffix="txt",
            source_type="web",
            metadata=metadata,
        )

    def _parse_entry(self, html: str, url: str) -> Tuple[str, Dict]:
        """Parse an ELOG entry page into clean text and structured metadata."""
        soup = BeautifulSoup(html, "html.parser")
        meta: dict = {"url": url, "elog_entry": True}

        # Extract entry ID from URL
        entry_id = url.rstrip("/").rsplit("/", 1)[-1]
        meta["entry_id"] = entry_id

        # Extract attribute rows (Incident Date, Tech, Node, Inst, Category, Fix Action)
        for row in soup.select("table.listframe tr td table tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                key = cells[0].get_text(strip=True).rstrip(":")
                value = cells[1].get_text(strip=True)
                if key and value:
                    meta[key.lower().replace(" ", "_")] = value

        # Main message body
        body = ""
        pre = soup.find("pre", class_="messagepre")
        if pre:
            body = pre.get_text()

        # Build clean plain-text document
        lines = [f"ELOG Entry {entry_id} — {self.base_url}"]
        for k, v in meta.items():
            if k not in ("url", "elog_entry", "entry_id"):
                lines.append(f"{k.replace('_', ' ').title()}: {v}")
        lines.append("")
        lines.append(body.strip())

        return "\n".join(lines), meta

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            r = self._session.get(url, timeout=15, verify=self.verify_ssl)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            logger.warning(f"ElogScraper: could not fetch {url}: {exc}")
            return None
