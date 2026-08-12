from importlib import import_module
import inspect
from pathlib import Path
import sys

from src.utils.logging import get_logger

from .base import check_tool_permission, require_tool_permission
from .local_files import (
    create_document_fetch_tool,
    create_file_search_tool,
    create_metadata_search_tool,
    create_metadata_schema_tool,
    RemoteCatalogClient,
)
from .retriever import create_retriever_tool
from .mcp import has_user_scoped_servers, initialize_mcp_client
from .monit_opensearch import (
    MONITOpenSearchClient,
    create_monit_opensearch_search_tool,
    create_monit_opensearch_aggregation_tool,
)
from .ingest import create_ingest_url_tool
from .indico_ingest import create_ingest_indico_event_tool
from .playbook_tools import (
    create_playbook_tool,
    create_playbook_listing_middleware,
    create_save_playbook_tool,
    create_update_playbook_tool,
    create_delete_playbook_tool,
    set_playbook_owner,
    get_playbook_owner,
)

logger = get_logger(__name__)

__all__ = [
    "check_tool_permission",
    "require_tool_permission",
    "create_document_fetch_tool",
    "create_file_search_tool",
    "create_metadata_search_tool",
    "create_metadata_schema_tool",
    "RemoteCatalogClient",
    "create_retriever_tool",
    "has_user_scoped_servers",
    "initialize_mcp_client",
    "MONITOpenSearchClient",
    "create_monit_opensearch_search_tool",
    "create_monit_opensearch_aggregation_tool",
    "create_ingest_url_tool",
    "create_ingest_indico_event_tool",
    "create_playbook_tool",
    "create_playbook_listing_middleware",
    "create_save_playbook_tool",
    "create_update_playbook_tool",
    "create_delete_playbook_tool",
    "set_playbook_owner",
    "get_playbook_owner",
]

_seen_names = set(__all__)

tools_dir = Path("/root/archi/src/archi/pipelines/agents/tools/extra_tools")

try:
    exists = tools_dir.is_dir()
except PermissionError:
    exists = False

if exists:
    extra_tools_dir = str(tools_dir.resolve())
    if extra_tools_dir not in sys.path:
        sys.path.insert(0, extra_tools_dir)

    for file_path in tools_dir.glob("*.py"):
        # Skip __init__.py itself and the explicitly imported modules to avoid double-processing
        if file_path.name == "__init__.py":
            continue

        module_path = file_path.stem

        try:
            # Dynamically import the module relative to this package
            module = import_module(module_path)
            logger.debug(f"Successfully imported module {module_path}")
        except Exception as e:
            logger.error(f"Failed to dynamically import module {module_path}: {e}")
            continue

        # Inspect the module for top-level members
        for name, obj in inspect.getmembers(module):
            # Enforce your rules: must be public, and must be either a class or a function
            if name.startswith("_"):
                continue

            if inspect.isclass(obj) or inspect.isfunction(obj):
                # Check for name collisions across the flattened namespace
                if name in _seen_names:
                    logger.warning(
                        f"Name collision detected! '{name}' in {module} "
                        f"was skipped because it is already registered."
                    )
                    continue

                # Inject the object directly into this module's global namespace
                globals()[name] = obj
                __all__.append(name)
                _seen_names.add(name)