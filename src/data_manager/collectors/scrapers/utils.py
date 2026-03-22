from typing import List
from urllib.parse import urlparse

from scrapy.http import Response

_IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico", ".webp"
})

def same_host_links(base_host, response: Response) -> List[str]:
    """
    Return deduplicated same-host, non-image absolute URLs on this page.
    """

    seen = set()
    links = []
    for href in response.css("a::attr(href)").getall():
        url = response.urljoin(href)
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
