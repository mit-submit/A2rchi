from scrapy.http import Response

IMAGE_EXTENSIONS = [
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico", ".webp"
]

# .pdf, docs , xlsx, pptx are first class supported by MarkItDown
IGNORED_DOCUMENT_EXTENSIONS = [
    ".doc",
    ".xls",
    ".ppt",
    ".zip",
    ".rar",
]

def get_content_type(response: Response) -> str:
    """Decode the Content-Type header bytes to str."""
    raw: bytes = response.headers.get("Content-Type", b"") or b""
    return raw.decode("utf-8", errors="replace")
