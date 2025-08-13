"""Chain for chatting with a vector database."""
from __future__ import annotations
from langchain_core.callbacks.file import FileCallbackHandler

from a2rchi.chains.utils.token_limiter import TokenLimiter
from a2rchi.chains.utils import history_utils
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger

from langchain_core.language_models.base import BaseLanguageModel
from langchain.chains.combine_documents.stuff import create_stuff_documents_chain
from langchain.chains.llm import LLMChain # deprecated, should update
from langchain.callbacks.manager import CallbackManagerForChainRun
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_core.retrievers import BaseRetriever
from langchain_core.prompts.base import BasePromptTemplate
from typing import Any, Dict, List, Optional, Tuple

import os 
from datetime import datetime

logger = get_logger(__name__)

# DEFINITIONS
config = load_config()["chains"]["base"]
data_path = load_config()["global"]["DATA_PATH"]

class BaseQAChain:
    """
    Chain for chatting with an index, designed originally for SubMIT, now used for general QA
    """

    def __init__(self, retriever: BaseRetriever, combine_docs_chain, condense_question_chain, llm: BaseLanguageModel, prompt: BasePromptTemplate):
        self.retriever = retriever
        self.combine_docs_chain = combine_docs_chain
        self.condense_question_chain = condense_question_chain
        self.llm = llm
        self.prompt = prompt
        logfile = os.path.join(data_path, config["logging"]["input_output_filename"])
        self.prompt_logger = PromptLogger(logfile)
        self.token_limiter = TokenLimiter(llm=self.llm, prompt=self.prompt)

    def invoke(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        question = inputs["question"]
        chat_history = inputs.get("chat_history", [])

        # if the size of the question is too large, return a warning
        if not self.token_limiter.check_question(question):
            return {
                "answer": self.token_limiter.QUESTION_SIZE_WARNING,
                "question": question,
                "chat_history": chat_history,
                "source_documents": []
            }

        if chat_history:
            _, reduced_history = self.token_limiter.reduce_tokens_below_limit(history=chat_history)
            formatted_reduced_history = history_utils.stringify_history(reduced_history)
            condensed_question = self.condense_question_chain.invoke(
                {"question": question, "chat_history": formatted_reduced_history},
                config={"callbacks": [self.prompt_logger]}
            )
            final_question = condensed_question
        else:
            final_question = question

        # retrieve documents
        docs = self.retriever.invoke(final_question) # n.b., using condensed question for retrieval

        # reduce number of tokens, if necessary
        reduced_docs, reduced_history = self.token_limiter.reduce_tokens_below_limit(docs=docs, history=chat_history)
        formatted_reduced_history = history_utils.stringify_history(reduced_history)
        
        answer = self.combine_docs_chain.invoke(
            {"question": final_question, "history": formatted_reduced_history, "context": reduced_docs},
            config={"callbacks": [self.prompt_logger]}
        )

        return {
            "answer": answer,
            "question": question,
            "chat_history": reduced_history,
            "source_documents": reduced_docs
        }

    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel,
        retriever: BaseRetriever,
        qa_prompt: BasePromptTemplate,
        condense_question_prompt: BasePromptTemplate,
        condense_question_llm: Optional[BaseLanguageModel] = None,
        combine_docs_chain_kwargs: Optional[Dict] = None,
        **kwargs: Any,
    ) -> "BaseQAChain":
        
        combine_docs_chain_kwargs = combine_docs_chain_kwargs or {}
        document_variable_name = "context"

        # store templated inputs to llm (condense and main) in this file
        logfile = os.path.join(data_path, config["logging"]["input_output_filename"])
        logger.info(f"Setting up BaseQAChain with log file for filled templates at: {logfile}, accessible outside the container at volume a2rchi-<name of your deployment>")

        doc_chain = create_stuff_documents_chain(
            llm=llm,
            prompt=qa_prompt,
            document_variable_name=document_variable_name,
            **combine_docs_chain_kwargs
        )

        _llm = condense_question_llm or llm
        condense_question_chain = condense_question_prompt | _llm | StrOutputParser()

        logger.debug("BaseQAChain created successfully")

        return cls(
            retriever=retriever,
            combine_docs_chain=doc_chain,
            condense_question_chain=condense_question_chain,
            llm=llm,
            prompt=qa_prompt
        )

    
class ImageLLMChain(LLMChain):
    """LLMChain but overriding _call method to ensure it points to custom LLM"""
    
    def _call(
        self,
        inputs: Dict[str, Any],
        run_manager: Optional[CallbackManagerForChainRun] = None,
    ) -> Dict[str, str]:
        images = inputs.get("images", [])
        
        # format prompt ourself now
        prompt_inputs = {k: v for k, v in inputs.items() if k != "images"}
        prompt = self.prompt.format(**prompt_inputs)
        
        # directly calling HuggingFaceImageLLM's _call method
        response = self.llm._call(
            prompt=prompt,
            images=images,
            run_manager=run_manager.get_child() if run_manager else None,
        )
        
        return {"text": response}

class BaseImageProcessingChain:
    """
    Chain for processing images.
    """

    def __init__(self, image_processing_chain: ImageLLMChain):
        """
        Initialize the image processing chain with the provided LLM chain.
        """
        self.image_processing_chain = image_processing_chain

    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel,
        prompt: BasePromptTemplate,
        verbose: bool = False,
        **kwargs: Any,
    ) -> "BaseImageProcessingChain":
        
        return cls(
            image_processing_chain=ImageLLMChain(
                llm=llm,
                prompt=prompt,
                verbose=verbose,
                **kwargs,
            )
        )

    def run(
        self,
        images: List[str, Any],
    ) -> Dict[str, str]:
        """
        Run the image processing chain with the provided images.
        """
        if not self.image_processing_chain:
            raise ValueError("Image processing chain is not defined.")
        
        logger.info(f"Processing {len(images)} images.")
        text_from_image = self.image_processing_chain.invoke(
            input={"images": images},
            config={}
        )

        return text_from_image
    
#prompt_text = self.image_processing_chain.llm_chain.prompt.format()
#
#        text_from_image = self.image_processing_chain._call(
#            prompt=prompt_text,
#            images=images,
#        )



class BaseGradingChain:
    """
    Construct chain for grading a response.
    """

    def __init__(self, summary_chain: LLMChain, analysis_chain: LLMChain, final_grade_chain: LLMChain, retriever: Optional[BaseRetriever] = None):
        """
        Initialize the grading chain with the summary, analysis, and final grade chains.
        """
        self.summary_chain = summary_chain
        self.analysis_chain = analysis_chain
        self.final_grade_chain = final_grade_chain
        self.retriever = retriever

    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel, # TODO: currently only supporting same llm for all grading steps, quick to update...
        final_grade_prompt: BasePromptTemplate,
        summary_prompt: Optional[BasePromptTemplate] = None,
        analysis_prompt: Optional[BasePromptTemplate] = None,
        retriever: Optional[BaseRetriever] = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> "BaseGradingChain":

        logfile = os.path.join(data_path, config["logging"]["input_output_filename"])
        logger.info(f"Setting up BaseGradingChain with log file for filled templates at: {logfile}")
        handler = FileCallbackHandler(logfile)

        # TODO: for supporting different LLMs for each step, define _llm = ... for passing to summary and analysis chains

        # build chains
        if summary_prompt is not None:
            summary_chain = LLMChain(
                llm=llm,
                prompt=summary_prompt,
                callbacks = [handler],
                verbose=verbose,
            )
        else:
            summary_chain = None

        if analysis_prompt is not None:
            analysis_chain = LLMChain(
                llm=llm,
                prompt=analysis_prompt,
                callbacks = [handler],
                verbose=verbose,
            )
        else:
            analysis_chain = None

        final_grade_chain = LLMChain(
            llm=llm,
            prompt=final_grade_prompt,
            callbacks = [handler],
            verbose=verbose,
        )

        logger.debug("BaseGradingChain created successfully")

        return cls(
            summary_chain=summary_chain,
            analysis_chain=analysis_chain,
            final_grade_chain=final_grade_chain,
            retriever=retriever
        )

    def run(
        self,
        submission_text: str,
        rubric_text: str,
        additional_comments: str = "",
    ) -> Dict[str, str]:
        """
        Run the grading chain with the provided submission text and rubric.
        """
        
        if not self.summary_chain:
            logger.info("Summary prompt, and thus chain, is not defined. Skipping summary step.")
        else:
            summary = self.summary_chain.run(
                submission_text=submission_text,
            )

        retrieved_docs = self.retriever.invoke(submission_text) if self.retriever else []

        token_limiter = TokenLimiter(
            llm=self.final_grade_chain.llm,
            max_tokens=self.final_grade_chain.llm.max_tokens if hasattr(self.final_grade_chain.llm, 'max_tokens') else 7000,
            reserved_tokens=self._estimate_grader_reserved_tokens(submission_text, rubric_text, summary if self.summary_chain else "", additional_comments)
        )

        reduced_docs = token_limiter.reduce_tokens_below_limit(retrieved_docs)


        if reduced_docs:
            retrieved_context = "\n\n".join(doc.page_content for doc in reduced_docs)

        if not self.analysis_chain:
            logger.info("Analysis prompt, and thus chain, is not defined. Skipping analysis step.")
        else:
            analysis = self.analysis_chain.run(
                submission_text=submission_text,
                rubric_text=rubric_text,
                summary=summary if self.summary_chain else "No solution summary provided. Complete the analysis without it.",
            )

        final_grade = self.final_grade_chain.run(
            rubric_text=rubric_text,
            submission_text=submission_text,
            analysis=analysis if self.analysis_chain else "No analysis summary, complete the final grading without it.",
            additional_comments=additional_comments,
        )

        return {
            "summary": summary if self.summary_chain else "No solution summary.",
            "analysis": analysis if self.analysis_chain else "No preliminary analysis step.",
            "final_grade": final_grade,
            "retrieved_context": retrieved_context if reduced_docs else "No relevant documents retrieved."
        }

    
    def _estimate_grader_reserved_tokens(self, submission_text: str, rubric_text: str, summary: str, additional_comments: str) -> int:
        """
        Estimate the number of reserved tokens based on the input texts.
        """
        reserved_tokens = 300
        reserved_tokens += self.final_grade_chain.llm.get_num_tokens(submission_text)
        reserved_tokens += self.final_grade_chain.llm.get_num_tokens(rubric_text)
        reserved_tokens += self.final_grade_chain.llm.get_num_tokens(summary)
        reserved_tokens += self.final_grade_chain.llm.get_num_tokens(additional_comments)

        logger.info(f"Estimated reserved tokens: {reserved_tokens}")

        return reserved_tokens
    

class PromptLogger(BaseCallbackHandler):
    """Lightweight callback handler to log prompts and responses to file"""
    
    def __init__(self, logfile: str):
        self.logfile = logfile
        os.makedirs(os.path.dirname(logfile), exist_ok=True)
    
    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
        """Log the prompt when LLM starts"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.logfile, 'a', encoding='utf-8') as f:
            f.write("-" * 41)
            f.write(f"\n[{timestamp}] Prompt sent to LLM:\n")
            f.write("-" * 41 + "\n\n")
            for prompt in prompts:
                f.write(f"{prompt}\n\n\n")
    
    def on_llm_end(self, response, **kwargs: Any) -> None:
        """Log the response when LLM ends"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.logfile, 'a', encoding='utf-8') as f:
            f.write("-" * 35)
            f.write(f"\n[{timestamp}] LLM Response:\n")
            f.write("-" * 35 + "\n\n")
            
            # handle different response formats
            if hasattr(response, 'generations'):
                for generation_list in response.generations:
                    for generation in generation_list:
                        if hasattr(generation, 'text'):
                            f.write(f"{generation.text}\n\n\n")
                        elif hasattr(generation, 'message'):
                            f.write(f"{generation.message.content}\n\n\n")
            else:
                f.write(f"{response}\n\n\n")
            
            f.write("=" * 96 + "\n\n\n")

    