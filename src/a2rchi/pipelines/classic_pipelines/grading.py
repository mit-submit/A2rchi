"""Grading pipeline implementation."""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.output_parsers import StrOutputParser

from src.a2rchi.pipelines.classic_pipelines.utils.chain_wrappers import ChainWrapper
from src.a2rchi.pipelines.classic_pipelines.base import BasePipeline
from src.a2rchi.utils.output_dataclass import PipelineOutput
from src.data_manager.vectorstore.retrievers import SemanticRetriever
from src.utils.logging import get_logger

logger = get_logger(__name__)


class GradingPipeline(BasePipeline):
    """Pipeline for grading a response using summary, analysis, and final grade steps."""

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(config, *args, **kwargs)
        self.summary_chain = None
        self.analysis_chain = None
        self.final_grade_chain = None
        self.retriever = None
        self._init_chains()

    def _init_chains(self) -> None:
        if 'summary_prompt' in self.prompts:
            self.summary_chain = ChainWrapper(
                chain=self.prompts['summary_prompt']
                | self.llms['final_grade_model']
                | StrOutputParser(),
                llm=self.llms['final_grade_model'],
                prompt=self.prompts['summary_prompt'],
                required_input_variables=['submission_text'],
                max_tokens=self.pipeline_config.get('max_tokens', 7000),
            )
        if 'analysis_prompt' in self.prompts:
            self.analysis_chain = ChainWrapper(
                chain=self.prompts['analysis_prompt']
                | self.llms['analysis_model']
                | StrOutputParser(),
                llm=self.llms['analysis_model'],
                prompt=self.prompts['analysis_prompt'],
                required_input_variables=['submission_text', 'rubric_text', 'summary'],
                max_tokens=self.pipeline_config.get('max_tokens', 7000),
            )
        self.final_grade_chain = ChainWrapper(
            chain=self.prompts['final_grade_prompt']
            | self.llms['final_grade_model']
            | StrOutputParser(),
            llm=self.llms['final_grade_model'],
            prompt=self.prompts['final_grade_prompt'],
            required_input_variables=['rubric_text', 'submission_text', 'analysis'],
            max_tokens=self.pipeline_config.get('max_tokens', 7000),
        )

    def update_retriever(self, vectorstore):
        retrievers_cfg = self.dm_config.get("retrievers", {})
        semantic_cfg = retrievers_cfg.get("semantic_retriever", {})
        default_k = self.dm_config.get("num_documents_to_retrieve", 4)
        self.retriever = SemanticRetriever(
            vectorstore=vectorstore,
            k=semantic_cfg.get("num_documents_to_retrieve", default_k),
            dm_config=self.dm_config,
        )

    def _estimate_grader_reserved_tokens(
        self,
        submission_text: str,
        rubric_text: str,
        summary: str,
        additional_comments: str,
    ) -> int:
        reserved_tokens = 300
        llm = self.llms['final_grade_model']
        reserved_tokens += llm.get_num_tokens(submission_text)
        reserved_tokens += llm.get_num_tokens(rubric_text)
        reserved_tokens += llm.get_num_tokens(summary)
        reserved_tokens += llm.get_num_tokens(additional_comments)
        logger.info("Estimated reserved tokens: %s", reserved_tokens)
        return reserved_tokens

    def invoke(
        self,
        submission_text: str,
        rubric_text: str,
        additional_comments: str = "",
        vectorstore=None,
        **kwargs,
    ) -> PipelineOutput:
        if vectorstore:
            self.update_retriever(vectorstore)

        summary = "No solution summary."
        if self.summary_chain:
            summary = self.summary_chain.invoke({
                "submission_text": submission_text,
            })['answer']

        retrieved_docs = []
        if self.retriever:
            retrieval = self.retriever.invoke(submission_text)
            if retrieval:
                retrieved_docs, _ = zip(*retrieval)
            else:
                retrieved_docs = []

        analysis = "No preliminary analysis step."
        if self.analysis_chain:
            analysis = self.analysis_chain.invoke({
                "submission_text": submission_text,
                "rubric_text": rubric_text,
                "summary": summary if self.summary_chain else "No solution summary provided. Complete the analysis without it.",
            })['answer']

        final_grade = self.final_grade_chain.invoke({
            "rubric_text": rubric_text,
            "submission_text": submission_text,
            "analysis": analysis if self.analysis_chain else "No analysis summary, complete the final grading without it.",
            "additional_comments": additional_comments,
        })['answer'] if self.final_grade_chain else "No final grade chain defined."

        documents = list(retrieved_docs) if retrieved_docs else []
        intermediate_steps = []
        if summary:
            intermediate_steps.append(summary)
        if analysis:
            intermediate_steps.append(analysis)

        return PipelineOutput(
            answer=final_grade,
            source_documents=documents,
            intermediate_steps=intermediate_steps,
            metadata={
                "summary": summary,
                "analysis": analysis,
                "additional_comments": additional_comments,
            },
        )
