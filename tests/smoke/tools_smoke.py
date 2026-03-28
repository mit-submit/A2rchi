#!/usr/bin/env python3
"""Direct tool smoke checks for catalog and vectorstore tools.

Updated for Copilot SDK: tools are now @define_tool-decorated async
functions. We test the underlying catalog/retriever operations directly
and verify that the tool factories produce callable objects.
"""

import asyncio
import os
import sys
from typing import Dict

import yaml

from src.archi.pipelines.agents.tools import RemoteCatalogClient
from src.archi.tools import (TOOL_REGISTRY, DocumentCollector,
                             build_document_fetch_tool, build_file_search_tool,
                             build_metadata_search_tool, build_retriever_tool)
from src.archi.utils.vectorstore_connector import VectorstoreConnector
from src.data_manager.vectorstore.retrievers import HybridRetriever


def _fail(message: str) -> None:
    print(f"[tools-smoke] ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def _info(message: str) -> None:
    print(f"[tools-smoke] {message}")


def _load_config() -> Dict:
    config_path = os.getenv("ARCHI_CONFIG_PATH")
    if not config_path:
        _fail("ARCHI_CONFIG_PATH is required for tool smoke checks")
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:
        _fail(f"Failed to load config at {config_path}: {exc}")
    return {}


def _map_embedding_classes(config: Dict) -> None:
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_openai import OpenAIEmbeddings
    except Exception as exc:
        _fail(f"Missing embedding dependencies: {exc}")

    embedding_map = config.get("data_manager", {}).get("embedding_class_map", {})
    for name, entry in embedding_map.items():
        class_name = entry.get("class") or entry.get("name") or name
        if class_name == "HuggingFaceEmbeddings":
            entry["class"] = HuggingFaceEmbeddings
        elif class_name == "OpenAIEmbeddings":
            entry["class"] = OpenAIEmbeddings
        else:
            _fail(f"Unsupported embedding class '{class_name}' in config")


def _build_catalog_client(config: Dict) -> RemoteCatalogClient:
    dm_base_url = os.getenv("DM_BASE_URL")
    if dm_base_url:
        return RemoteCatalogClient(base_url=dm_base_url)
    return RemoteCatalogClient.from_deployment_config(config)


def _run_catalog_tools(catalog: RemoteCatalogClient) -> None:
    file_query = os.getenv("FILE_SEARCH_QUERY", "Smoke test seed document")
    metadata_query = os.getenv("METADATA_SEARCH_QUERY", "file_name:seed.txt")

    # Verify tool factories produce Tool objects
    collector = DocumentCollector()
    file_search_tool = build_file_search_tool(catalog, store_docs=collector.store_docs)
    metadata_search_tool = build_metadata_search_tool(
        catalog, store_docs=collector.store_docs
    )
    fetch_tool = build_document_fetch_tool(catalog)
    assert file_search_tool is not None, "build_file_search_tool returned None"
    assert metadata_search_tool is not None, "build_metadata_search_tool returned None"
    assert fetch_tool is not None, "build_document_fetch_tool returned None"
    _info("Tool factories produce Tool objects ✓")

    # Test underlying catalog operations directly
    _info("Running catalog file search ...")
    file_results = catalog.search(file_query, limit=3, search_content=True)
    file_hits = list(file_results)
    if not file_hits:
        _fail("Catalog file search returned no results")
    _info(f"  Found {len(file_hits)} file(s)")

    _info("Running catalog metadata search ...")
    meta_results = catalog.search(metadata_query, limit=3, search_content=False)
    meta_hits = list(meta_results)
    if not meta_hits:
        _fail("Catalog metadata search returned no results")
    _info(f"  Found {len(meta_hits)} metadata hit(s)")

    _info("Running document fetch ...")
    resource_hash = meta_hits[0].get("hash")
    if not resource_hash:
        _fail("Catalog hit missing resource hash")
    doc = catalog.get_document(resource_hash, max_chars=4000)
    if not doc or not doc.get("text"):
        _fail("Document fetch returned empty content")
    _info(f"  Fetched document ({len(doc['text'])} chars)")


def _run_vectorstore_tool(config: Dict) -> None:
    _map_embedding_classes(config)
    vectorstore = VectorstoreConnector(config).get_vectorstore()

    retriever_cfg = (
        config.get("data_manager", {}).get("retrievers", {}).get("hybrid_retriever")
    )
    if not retriever_cfg:
        _fail(
            "Missing data_manager.retrievers.hybrid_retriever config for vectorstore tool"
        )

    hybrid_retriever = HybridRetriever(
        vectorstore=vectorstore,
        k=retriever_cfg["num_documents_to_retrieve"],
        bm25_weight=retriever_cfg["bm25_weight"],
        semantic_weight=retriever_cfg["semantic_weight"],
    )

    # Verify factory produces a Tool object
    collector = DocumentCollector()
    retriever_tool = build_retriever_tool(hybrid_retriever, store_docs=collector.store_docs)
    assert retriever_tool is not None, "build_retriever_tool returned None"
    _info("Retriever tool factory produces Tool object ✓")

    # Test underlying retriever directly
    query = os.getenv("VECTORSTORE_QUERY", "Smoke test seed document")
    _info("Running vectorstore retriever ...")
    results = hybrid_retriever.invoke(query)
    if not results:
        _fail("Vectorstore retriever returned no documents")
    _info(f"  Retrieved {len(results)} document(s)")


def _verify_tool_registry() -> None:
    """Verify TOOL_REGISTRY is consistent and all factories are callable."""
    _info("Verifying TOOL_REGISTRY ...")
    expected_tools = {
        "search_knowledge_base",
        "search_local_files",
        "search_metadata_index",
        "list_metadata_schema",
        "fetch_catalog_document",
        "monit_opensearch_search",
        "monit_opensearch_aggregation",
    }
    actual_tools = set(TOOL_REGISTRY.keys())
    if actual_tools != expected_tools:
        missing = expected_tools - actual_tools
        extra = actual_tools - expected_tools
        _fail(f"TOOL_REGISTRY mismatch. Missing: {missing}, Extra: {extra}")

    for name, entry in TOOL_REGISTRY.items():
        if not callable(entry.get("factory")):
            _fail(f"TOOL_REGISTRY['{name}'].factory is not callable")
        if not isinstance(entry.get("description"), str):
            _fail(f"TOOL_REGISTRY['{name}'].description is not a string")
    _info(f"  All {len(TOOL_REGISTRY)} tools registered correctly ✓")


def main() -> None:
    config = _load_config()
    _verify_tool_registry()
    catalog = _build_catalog_client(config)
    _run_catalog_tools(catalog)
    _run_vectorstore_tool(config)
    _info("Tool smoke checks passed")


if __name__ == "__main__":
    main()
