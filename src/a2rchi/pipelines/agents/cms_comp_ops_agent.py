from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence

from langchain_core.documents import Document

from src.utils.logging import get_logger
from src.a2rchi.pipelines.agents.base import BaseAgent
from src.data_manager.vectorstore.retrievers import SemanticRetriever, BM25LexicalRetriever
from src.a2rchi.pipelines.agents.tools import (
    create_file_search_tool,
    create_metadata_search_tool,
    create_retriever_tool,
)
from src.a2rchi.pipelines.agents.utils.history_utils import infer_speaker
from src.data_manager.collectors.utils.index_utils import CatalogService

logger = get_logger(__name__)


class CMSCompOpsAgent(BaseAgent):
    """Agent designed for CMS CompOps operations."""

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(config, *args, **kwargs)

        self.catalog_service = CatalogService(
            data_path=self.config["global"]["DATA_PATH"]
        )
        self._vector_retrievers = None
        self._vector_tool = None

        self.rebuild_static_tools()
        self.refresh_agent()

    def _build_static_tools(self) -> List[Callable]:
        """Initialise static tools that are always available to the agent."""
        file_search_tool = create_file_search_tool(
            self.catalog_service,
            description= (
                "Scan raw file contents for an exact regex match. Provide a distinctive snippet or error message exactly "
                "as it appears in the file (escape regex metacharacters if needed) so the matcher can zero in on the right lines."
                "Returns the matching files and their metadata."
            ),
            store_docs=self._store_documents,
        )
        metadata_search_tool = create_metadata_search_tool(
            self.catalog_service,
            description=(
                "Query the files' metadata catalog (ticket IDs, source URLs, resource types, etc.). "
                "Supply the specific identifier or keyword you expect to find in metadata."
                "Returns the matching files and their metadata."
                "Example metadata:"
                "{created_at: 2023-03-08T11:43:18.000+0100"
                "display_name: https://its.cern.ch/jira/browse/CMSTRANSF-527"
                "project: CMSTRANSF"
                "source_type: jira"
                "ticket_id: CMSTRANSF-527"
                "url: https://its.cern.ch/jira/browse/CMSTRANSF-527}"
            ),
            store_docs=self._store_documents,
        )
        return [file_search_tool, metadata_search_tool]

    def _store_documents(self, stage: str, docs: Sequence[Document]) -> None:
        """Centralised helper used by tools to record documents into the active memory."""
        memory = self.active_memory
        if not memory:
            return
        # Prefer memory convenience method if available
        try:
            logger.debug("Recording %d documents from stage '%s' via record_documents", len(docs), stage)
            memory.record_documents(stage, docs)
        except Exception:
            # fallback to explicit record + note
            memory.record(stage, docs)
            memory.note(f"{stage} returned {len(list(docs))} document(s).")

    def _prepare_inputs(self, history: Any, **kwargs) -> Dict[str, Any]:
        """Create list of messages using LangChain's formatting."""
        history = history or []
        history_messages = [infer_speaker(msg[0])(msg[1]) for msg in history]
        return {"history": history_messages}

    def _prepare_agent_inputs(self, **kwargs) -> Dict[str, Any]:
        """Prepare agent state and formatted inputs shared by invoke/stream."""
        
        # event-level memory (which documents were retrieved)
        memory = self.start_run_memory()
       
        # refresh vs connection
        vectorstore = kwargs.get("vectorstore")
        if vectorstore:
            self._update_vector_retrievers(vectorstore)
        else:
            self._vector_retrievers = None
            self._vector_tools = None
        extra_tools = self._vector_tools if self._vector_tools else None

        # ensure the latest files are indexed for the tools' use
        self.catalog_service.refresh()
        memory.note("Catalog refreshed for agent run.")

        self.refresh_agent(extra_tools=extra_tools)

        inputs = self._prepare_inputs(history=kwargs.get("history"))
        history_messages = inputs["history"]
        if history_messages:
            memory.note(f"History contains {len(history_messages)} message(s).")
            last_message = history_messages[-1]
            content = self._message_content(last_message)
            if content:
                snippet = content if len(content) <= 200 else f"{content[:197]}..."
                memory.note(f"Latest user message: {snippet}")
        return {"messages": history_messages}

    def _update_vector_retrievers(self, vectorstore: Any) -> None:
        """Instantiate or refresh the vectorstore retriever tool."""
        retrievers_cfg = self.dm_config.get("retrievers", {})
        default_k = 5

        semantic_cfg = retrievers_cfg.get("semantic_retriever", {})
        bm25_cfg = retrievers_cfg.get("bm25_retriever", {})

        semantic_k = semantic_cfg.get("num_documents_to_retrieve", default_k)
        bm25_k = bm25_cfg.get("num_documents_to_retrieve", default_k)

        semantic_retriever = SemanticRetriever(
            vectorstore=vectorstore,
            k=semantic_k,
            dm_config=self.dm_config,
        )

        bm25_retriever = BM25LexicalRetriever(
            vectorstore=vectorstore,
            k=bm25_k,
            bm25_k1=bm25_cfg.get("k1", 0.5),
            bm25_b=bm25_cfg.get("b", 0.75),
        )

        semantic_description = (
            "Semantic vector search over the knowledge base. Use when you want concept-level matches, "
            "related procedures, or paraphrased content. Include the task, component, and any identifiers (ticket IDs, "
            "filenames, services) so embedding search can pull in closely related context."
        )
        bm25_description = (
            "Lexical BM25 search over the knowledge base. Use when you have sharp keywords: exact error messages, log lines, "
            "config flags, ticket IDs, filenames, or function/class names. Provide the precise tokens as they appear to "
            "maximize keyword matching."
        )

        self._vector_retrievers = [semantic_retriever, bm25_retriever]
        self._vector_tools = []
        self._vector_tools.append(
            create_retriever_tool(
                semantic_retriever,
                name="search_vectorstore_semantic",
                description=semantic_description,
                store_docs=self._store_documents,
            )
        )
        self._vector_tools.append(
            create_retriever_tool(
                bm25_retriever,
                name="search_vectorstore_lexical",
                description=bm25_description,
                store_docs=self._store_documents,
            )
        )
