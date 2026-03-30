from scrapy.http import Response

_IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico", ".webp"
})

def get_content_type(response: Response) -> str:
    """Decode the Content-Type header bytes to str."""
    raw: bytes = response.headers.get("Content-Type", b"") or b""
    return raw.decode("utf-8", errors="replace")
