"""Question answering pipeline."""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_classic.chains.combine_documents.stuff import create_stuff_documents_chain
from langchain_core.output_parsers import StrOutputParser

from src.a2rchi.pipelines.classic_pipelines.utils.chain_wrappers import ChainWrapper
from src.a2rchi.pipelines.classic_pipelines.base import BasePipeline
from src.a2rchi.utils.output_dataclass import PipelineOutput
from src.data_manager.vectorstore.retrievers import SemanticRetriever, HybridRetriever
from src.a2rchi.pipelines.classic_pipelines.utils import history_utils
from src.utils.logging import get_logger

logger = get_logger(__name__)


class QAPipeline(BasePipeline):
    """Pipeline that condenses history, retrieves documents, and answers questions."""

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(config, *args, **kwargs)

        self.condense_chain = ChainWrapper(
            chain=self.prompts['condense_prompt']
            | self.llms['condense_model']
            | StrOutputParser(),
            llm=self.llms['condense_model'],
            prompt=self.prompts['condense_prompt'],
            required_input_variables=['history'],
            max_tokens=self.pipeline_config['max_tokens'],
        )
        self.chat_chain = ChainWrapper(
            chain=create_stuff_documents_chain(
                llm=self.llms['chat_model'],
                prompt=self.prompts['chat_prompt'],
                document_variable_name="retriever_output",
            ),
            llm=self.llms['chat_model'],
            prompt=self.prompts['chat_prompt'],
            required_input_variables=['question'],
            unprunable_input_variables=['question'],
            max_tokens=self.pipeline_config['max_tokens'],
        )

    def _prepare_inputs(self, history: Any, **kwargs) -> Dict[str, Any]:
        full_history = history_utils.tuplize_history(history)
        if len(full_history) > 0 and len(full_history[-1]) > 1:
            question = full_history[-1][1]
        else:
            logger.error("No question found")
            question = ""
        truncated_history = full_history[:-1] if full_history is not None else None

        return {
            "question": question,
            "history": truncated_history,
            "full_history": full_history,
        }

    def update_retriever(self, vectorstore):
        retrievers_cfg = self.dm_config.get("retrievers", {})
        hybrid_cfg = retrievers_cfg.get("hybrid_retriever", {})
        bm25_cfg = retrievers_cfg.get("bm25_retriever", {})
        default_k = 5

        logger.info("Initializing HybridRetriever with BM25 + semantic search")
        self.retriever = HybridRetriever(
            vectorstore=vectorstore,
            k=hybrid_cfg.get("num_documents_to_retrieve", default_k),
            bm25_weight=hybrid_cfg.get("bm25_weight", 0.6),
            semantic_weight=hybrid_cfg.get("semantic_weight", 0.4),
            bm25_k1=bm25_cfg.get("k1", 0.5),
            bm25_b=bm25_cfg.get("b", 0.75),
        )

    def invoke(self, **kwargs) -> PipelineOutput:
        vectorstore = kwargs.get("vectorstore")
        if vectorstore:
            self.update_retriever(vectorstore)

        inputs = self._prepare_inputs(history=kwargs.get("history"))

        condense_output = self.condense_chain.invoke({**inputs})
        retriever_output = self.retriever.invoke(condense_output['answer'])
        documents: List = []
        scores: List = []
        if retriever_output:
            retrieved_docs, retrieved_scores = zip(*retriever_output)
            documents = list(retrieved_docs)
            scores = list(retrieved_scores)

        answer_output = self.chat_chain.invoke({
            **inputs,
            'condense_output': condense_output['answer'],
            'retriever_output': documents if documents else "",
        })

        return PipelineOutput(
            answer=answer_output['answer'],
            source_documents=documents,
            messages=[],
            metadata={
                "retriever_scores": scores,
                "condensed_output": condense_output['answer'],
                "question": inputs.get("question", ""),
            },
        )
