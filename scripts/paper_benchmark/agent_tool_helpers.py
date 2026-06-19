"""Tool-construction helpers for the promoted paper agent benchmark driver.

This module is intentionally not a standalone benchmark runner. It exposes the
catalog, MONIT/OpenSearch, Rucio MCP, and inline skill-reference pieces used by
`run_agent.py` while keeping the corrected paper tier free of the old
`read_skill` tool surface.
"""

import importlib.util
import os
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient


REPO = Path(os.environ.get("ORCD_REPO", os.path.expanduser("~/A2rchi")))
AGENT_SPEC = REPO / "examples/agents/cms-comp-ops.md"
RUCIO_MCP_SKILL = REPO / "examples/skills/rucio_mcp.md"
RUCIO_EVENTS_SKILL = REPO / "examples/skills/rucio_events.md"
CONDOR_METRIC_SKILL = REPO / "examples/skills/condor_raw_metric.md"
QUESTIONS = REPO / "configs/submit75/curated_questions_270.json"


class _StubLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


def _install_import_shims() -> None:
    """Install the minimal shims needed for direct tool-module imports."""
    src_mod = sys.modules.setdefault("src", type(sys)("src"))
    src_mod.__path__ = [str(REPO / "src")]
    src_utils_mod = sys.modules.setdefault("src.utils", type(sys)("src.utils"))
    src_utils_mod.__path__ = [str(REPO / "src/utils")]
    utils_logging = type(sys)("src.utils.logging")
    utils_logging.get_logger = lambda name="_": _StubLogger()
    sys.modules["src.utils.logging"] = utils_logging

    utils_env = type(sys)("src.utils.env")
    utils_env.read_secret = lambda *args, **kwargs: None
    sys.modules["src.utils.env"] = utils_env

    src_archi_mod = sys.modules.setdefault("src.archi", type(sys)("src.archi"))
    src_archi_mod.__path__ = [str(REPO / "src/archi")]
    src_pipelines_mod = sys.modules.setdefault("src.archi.pipelines", type(sys)("src.archi.pipelines"))
    src_pipelines_mod.__path__ = [str(REPO / "src/archi/pipelines")]
    src_agents_mod = sys.modules.setdefault("src.archi.pipelines.agents", type(sys)("src.archi.pipelines.agents"))
    src_agents_mod.__path__ = [str(REPO / "src/archi/pipelines/agents")]
    src_tools_mod = sys.modules.setdefault("src.archi.pipelines.agents.tools", type(sys)("src.archi.pipelines.agents.tools"))
    src_tools_mod.__path__ = [str(REPO / "src/archi/pipelines/agents/tools")]

    base_mod = type(sys)("src.archi.pipelines.agents.tools.base")

    def passthrough_decorator(_permission):
        def decorate(fn):
            return fn

        return decorate

    base_mod.require_tool_permission = passthrough_decorator
    sys.modules["src.archi.pipelines.agents.tools.base"] = base_mod


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_install_import_shims()

_monit_mod = _load_module(
    "_paper_monit_module",
    REPO / "src/archi/pipelines/agents/tools/monit_opensearch.py",
)
MONITOpenSearchClient = _monit_mod.MONITOpenSearchClient
create_monit_opensearch_search_tool = _monit_mod.create_monit_opensearch_search_tool
create_monit_opensearch_aggregation_tool = _monit_mod.create_monit_opensearch_aggregation_tool
create_monit_fetch_document_tool = _monit_mod.create_monit_fetch_document_tool

_local_files_mod = _load_module(
    "_paper_local_files_module",
    REPO / "src/archi/pipelines/agents/tools/local_files.py",
)
RemoteCatalogClient = _local_files_mod.RemoteCatalogClient
create_grep_tool = _local_files_mod.create_grep_tool
create_metadata_search_tool = _local_files_mod.create_metadata_search_tool
create_metadata_schema_tool = _local_files_mod.create_metadata_schema_tool
create_document_fetch_tool = _local_files_mod.create_document_fetch_tool


SKILL_REGISTRY = {
    "rucio_mcp": {
        "path": RUCIO_MCP_SKILL,
        "trigger": "Read-only Rucio MCP contract and reproducibility convention.",
    },
    "rucio_events": {
        "path": RUCIO_EVENTS_SKILL,
        "trigger": "Field reference and Lucene query patterns for CMS Rucio MONIT events.",
    },
    "condor_raw_metric": {
        "path": CONDOR_METRIC_SKILL,
        "trigger": "Field reference and Lucene query patterns for CMS Condor MONIT metrics.",
    },
}


async def collect_rucio_tools():
    url = os.environ.get("ARCHI_RUCIO_MCP_URL", "http://127.0.0.1:8000/mcp")
    cfg = {"rucio": {"transport": "streamable_http", "url": url}}
    client = MultiServerMCPClient(cfg)
    return await client.get_tools(server_name="rucio")


def build_monit_tools():
    token = os.environ.get("MONIT_GRAFANA_TOKEN")
    if not token:
        print("WARN: MONIT_GRAFANA_TOKEN missing; MONIT tools will not be wired", file=sys.stderr)
        return []

    monit_client = MONITOpenSearchClient(
        token=token,
        url="https://monit-grafana.cern.ch/api/datasources/proxy/9269/_msearch",
    )
    condor_client = MONITOpenSearchClient(
        token=token,
        url="https://monit-grafana.cern.ch/api/datasources/proxy/8787/_msearch",
    )

    rucio_idx = "monit_prod_cms_rucio_raw_events*"
    condor_idx = "monit_prod_condor_raw_metric*"
    return [
        create_monit_opensearch_search_tool(
            monit_client, tool_name="rucio_events_search", index=rucio_idx, skill=None
        ),
        create_monit_opensearch_aggregation_tool(
            monit_client, tool_name="rucio_events_aggregation", index=rucio_idx, skill=None
        ),
        create_monit_fetch_document_tool(
            monit_client, tool_name="fetch_rucio_document", index=rucio_idx
        ),
        create_monit_opensearch_search_tool(
            condor_client, tool_name="condor_metric_search", index=condor_idx, skill=None
        ),
        create_monit_opensearch_aggregation_tool(
            condor_client, tool_name="condor_metric_aggregation", index=condor_idx, skill=None
        ),
        create_monit_fetch_document_tool(
            condor_client, tool_name="fetch_condor_document", index=condor_idx
        ),
    ]
