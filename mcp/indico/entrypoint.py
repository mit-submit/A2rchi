"""
Boot wrapper for mcp4indico's streamable-http mode.

Upstream's `indico_mcp_server.py` only reads INDICO_BASE_URL / BEARER_TOKEN /
API_KEY / API_SECRET inside `main()`, which runs from `if __name__ == '__main__'`
(stdio path). When uvicorn imports the module to serve `http_app`, that init
never fires and every tool call returns
"❌ Please configure the Indico connection first using the 'configure' tool".

This wrapper imports the upstream module, populates its module-level globals
from env vars, constructs the API client, then re-exposes `http_app`.
"""
import os

import indico_mcp_server as ims
from indico_api import IndicoAPI, IndicoClient

ims.INDICO_BASE_URL = os.getenv("INDICO_BASE_URL", "")
ims.BEARER_TOKEN = os.getenv("BEARER_TOKEN", "")
ims.API_KEY = os.getenv("API_KEY", "")
ims.API_SECRET = os.getenv("API_SECRET", "")

if ims.INDICO_BASE_URL and (ims.BEARER_TOKEN or (ims.API_KEY and ims.API_SECRET)):
    ims.api = IndicoAPI(
        ims.INDICO_BASE_URL,
        bearer_token=ims.BEARER_TOKEN or None,
        api_key=ims.API_KEY or None,
        api_secret=ims.API_SECRET or None,
    )
    ims.client = IndicoClient(ims.api)

http_app = ims.http_app
