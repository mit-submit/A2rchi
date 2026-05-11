FROM python:3.11-slim

ARG MCP4INDICO_REPO=https://gitlab.cern.ch/itgpt/mcp4indico.git
ARG MCP4INDICO_REF=800c5fc3fa351bc9be7eea026842e147e5254b06

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "mcp~=1.9.4" \
        "fastapi~=0.115.2" \
        "httpx" \
        "uvicorn"

WORKDIR /opt
RUN git clone "${MCP4INDICO_REPO}" mcp4indico \
 && git -C mcp4indico checkout "${MCP4INDICO_REF}"

# mcp4indico imports `indico_api` as a sibling module (not a package),
# so the server must be launched from inside indico_mcp/.
WORKDIR /opt/mcp4indico/indico_mcp

# Wrapper that reads env vars at HTTP-app import time. See entrypoint.py for why.
COPY entrypoint.py ./entrypoint.py

# Shared volume target for INDICO_get_files downloads. Compose mounts a named
# volume here that the data-manager also mounts (read-only) so its
# /document_index/ingest_local_path endpoint can ingest the just-downloaded files.
RUN mkdir -p /shared/indico-downloads
VOLUME ["/shared/indico-downloads"]

EXPOSE 8012

CMD ["uvicorn", "entrypoint:http_app", \
     "--host", "0.0.0.0", "--port", "8012", \
     "--log-level", "info"]
