"""Copilot SDK tool factories and the central TOOL_REGISTRY.

The registry maps canonical tool names to ``{"factory": callable, "description": str}``
entries.  ``CopilotAgentPipeline.get_tool_registry()`` reads this mapping so that
the agent spec editor can display available tools and their descriptions.
"""

from src.archi.tools.document_collector import DocumentCollector
from src.archi.tools.file_search import (
    DOCUMENT_FETCH_DESCRIPTION,
    DOCUMENT_FETCH_NAME,
    FILE_SEARCH_DESCRIPTION,
    FILE_SEARCH_NAME,
    METADATA_SCHEMA_DESCRIPTION,
    METADATA_SCHEMA_NAME,
    METADATA_SEARCH_DESCRIPTION,
    METADATA_SEARCH_NAME,
    build_document_fetch_tool,
    build_file_search_tool,
    build_metadata_schema_tool,
    build_metadata_search_tool,
)
from src.archi.tools.monit_search import (
    AGGREGATION_TOOL_DESCRIPTION,
    AGGREGATION_TOOL_NAME,
    SEARCH_TOOL_DESCRIPTION,
    SEARCH_TOOL_NAME,
    build_monit_aggregation_tool,
    build_monit_search_tool,
)
from src.archi.tools.retriever import (
    TOOL_DESCRIPTION as RETRIEVER_DESCRIPTION,
    TOOL_NAME as RETRIEVER_NAME,
    build_retriever_tool,
)

# Central tool registry: name → {factory, description}.
# Each factory is a callable(**deps) → @define_tool-decorated function.
TOOL_REGISTRY = {
    RETRIEVER_NAME: {
        "factory": build_retriever_tool,
        "description": RETRIEVER_DESCRIPTION,
    },
    FILE_SEARCH_NAME: {
        "factory": build_file_search_tool,
        "description": FILE_SEARCH_DESCRIPTION,
    },
    METADATA_SEARCH_NAME: {
        "factory": build_metadata_search_tool,
        "description": METADATA_SEARCH_DESCRIPTION,
    },
    METADATA_SCHEMA_NAME: {
        "factory": build_metadata_schema_tool,
        "description": METADATA_SCHEMA_DESCRIPTION,
    },
    DOCUMENT_FETCH_NAME: {
        "factory": build_document_fetch_tool,
        "description": DOCUMENT_FETCH_DESCRIPTION,
    },
    SEARCH_TOOL_NAME: {
        "factory": build_monit_search_tool,
        "description": SEARCH_TOOL_DESCRIPTION,
    },
    AGGREGATION_TOOL_NAME: {
        "factory": build_monit_aggregation_tool,
        "description": AGGREGATION_TOOL_DESCRIPTION,
    },
}

__all__ = [
    "TOOL_REGISTRY",
    "DocumentCollector",
    "build_retriever_tool",
    "build_file_search_tool",
    "build_metadata_search_tool",
    "build_metadata_schema_tool",
    "build_document_fetch_tool",
    "build_monit_search_tool",
    "build_monit_aggregation_tool",
]
