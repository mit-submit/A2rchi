"""
Boot wrapper for mcp4indico's streamable-http mode.

Upstream's `indico_mcp_server.py` only reads INDICO_BASE_URL / BEARER_TOKEN /
API_KEY / API_SECRET inside `main()`, which runs from `if __name__ == '__main__'`
(stdio path). When uvicorn imports the module to serve `http_app`, that init
never fires and every tool call returns
"❌ Please configure the Indico connection first using the 'configure' tool".

This wrapper imports the upstream module, populates its module-level globals
from env vars, constructs the API client, then re-exposes `http_app`.

It also overrides `IndicoClient.get_files` so that downloads always land in a
shared volume (`/shared/indico-downloads/<event_id>/` by default). The
data-manager container mounts the same volume and ingests from there via
`POST /document_index/ingest_local_path`. Without this override, files
would write to the upstream default `downloads/<event_id>/` relative to the
container CWD and be invisible to any other service.
"""
import logging
import os
import sys

import indico_mcp_server as ims
from indico_api import IndicoAPI, IndicoClient

# stdout/stderr go straight to `docker logs` for this container, so log to stderr
# at WARNING so volume-mount problems are immediately obvious.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[entrypoint] %(levelname)s %(message)s")
_log = logging.getLogger("entrypoint")

ims.INDICO_BASE_URL = os.getenv("INDICO_BASE_URL", "")
ims.BEARER_TOKEN = os.getenv("BEARER_TOKEN", "")
ims.API_KEY = os.getenv("API_KEY", "")
ims.API_SECRET = os.getenv("API_SECRET", "")

SHARED_DOWNLOADS_DIR = os.getenv("INDICO_DOWNLOADS_DIR", "/shared/indico-downloads")

if ims.INDICO_BASE_URL and (ims.BEARER_TOKEN or (ims.API_KEY and ims.API_SECRET)):
    ims.api = IndicoAPI(
        ims.INDICO_BASE_URL,
        bearer_token=ims.BEARER_TOKEN or None,
        api_key=ims.API_KEY or None,
        api_secret=ims.API_SECRET or None,
    )
    ims.client = IndicoClient(ims.api)

    # Force get_files() downloads into the shared volume so the data-manager
    # can ingest them via the cross-container path. The upstream MCP tool
    # schema doesn't expose `download_dir`, so we patch it at the client level.
    _orig_get_files = ims.client.get_files

    async def _patched_get_files(event_id, download_files=False, download_dir=None, include_content=True):
        if download_files and not download_dir:
            target = os.path.join(SHARED_DOWNLOADS_DIR, str(event_id))
            try:
                os.makedirs(target, exist_ok=True)
                download_dir = target
            except OSError as exc:
                # Loud about it: silent fallback means the data-manager can't see
                # the files and the agent gets a confusing "0 files found".
                _log.warning(
                    "Cannot create shared download dir %s: %s. "
                    "Falling back to upstream default 'downloads/%s' inside the container — "
                    "the data-manager will NOT see these files. Check that the named volume "
                    "'%s-{deployment}' is declared in compose and mounted RW on this service.",
                    target, exc, event_id, os.path.basename(SHARED_DOWNLOADS_DIR.rstrip('/')),
                )
                download_dir = None
        return await _orig_get_files(
            event_id,
            download_files=download_files,
            download_dir=download_dir,
            include_content=include_content,
        )

    ims.client.get_files = _patched_get_files

http_app = ims.http_app
