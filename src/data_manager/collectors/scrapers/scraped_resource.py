from dataclasses import dataclass, field
from typing import Any, Dict, Union
import hashlib
import re
from urllib.parse import urlparse

from src.data_manager.collectors.resource_base import BaseResource
from src.data_manager.collectors.utils.metadata import ResourceMetadata


@dataclass
class ScrapedResource(BaseResource):
    """Represents a single piece of scraped content."""

    url: str
    content: Union[str, bytes]
    suffix: str
    source_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_binary(self) -> bool:
        """Return True if the content payload should be written as bytes."""
        return isinstance(self.content, (bytes, bytearray))

    def get_hash(self) -> str:
        identifier = hashlib.md5()
        identifier.update(self.url.encode("utf-8"))
        return str(int(identifier.hexdigest(), 16))[:12]

    def get_filename(self) -> str:
        suffix = self.suffix.lstrip(".")
        return f"{self.get_hash()}.{suffix}"

    def get_content(self) -> Union[str, bytes]:
        return self.content

    def get_metadata(self) -> ResourceMetadata:
        extra = {str(k): str(v) for k, v in (self.metadata or {}).items()}
        extra.setdefault("url", self.url)
        extra.setdefault("suffix", self.suffix)
        extra.setdefault("source_type", self.source_type)
        display_name = extra.get("display_name")
        if display_name is None:
            display_name = self._format_link_display(self.url)
        return ResourceMetadata(display_name=display_name, extra=extra)
    
    @staticmethod
    def _format_link_display(link: str) -> str:
        parsed_link = urlparse(link)
        display_name = parsed_link.hostname or link
        if parsed_link.path and parsed_link.path != '/':
            first_path = parsed_link.path.strip('/').split('/')[0]
            display_name += f"/{first_path}"
        return display_name
