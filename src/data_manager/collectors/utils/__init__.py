from pathlib import Path
from typing import List

def extract_urls_from_file(path: Path) -> List[str]:
    """
    Extracts URLs from a file.
    """
    urls = []
    with path.open("r") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            urls.append(stripped.split(",")[0].strip())
    return urls
