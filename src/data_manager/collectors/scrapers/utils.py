from typing import List
from urllib.parse import urlparse

from scrapy.http import Response
from src.data_manager.collectors.scrapers.types import Url

_IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico", ".webp"
})

def same_host_links(base_host: str, urls: list[Url]) -> list[Url]:
    """
    Return deduplicated same-host, non-image absolute URLs preserving the original order.
    """

    seen = set()
    links = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.netloc != base_host:
            continue
        if any(parsed.path.lower().endswith(e) for e in _IMAGE_EXTS):
            continue
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links

def get_content_type(response: Response) -> str:
    """Decode the Content-Type header bytes to str."""
    raw: bytes = response.headers.get("Content-Type", b"") or b""
    return raw.decode("utf-8", errors="replace")
