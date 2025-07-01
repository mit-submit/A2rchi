"""Chain for chatting with a vector database."""
from __future__ import annotations
from pydantic import BaseModel
from loguru import logger
from langchain_core.callbacks.file import FileCallbackHandler

from a2rchi.chains.prompts import PROMPTS
from a2rchi.chains.utils.token_limiter import TokenLimiter
from a2rchi.utils.config_loader import Config_Loader

from langchain_core.language_models.base import BaseLanguageModel
from langchain.chains.combine_documents.stuff import StuffDocumentsChain # deprecated, should update
from langchain.chains.conversational_retrieval.base import BaseConversationalRetrievalChain
from langchain.chains.llm import LLMChain # deprecated, should update
from langchain.callbacks.manager import CallbackManagerForChainRun
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.prompts.base import BasePromptTemplate
from langchain_core.runnables import RunnableSequence, RunnablePassthrough
from typing import Any, Dict, List, Optional, Tuple
from typing import Callable
import os 


# DEFINITIONS
config = Config_Loader().config["chains"]["base"]
data_path = Config_Loader().config["global"]["DATA_PATH"]


def _get_chat_history(chat_history: List[Tuple[str, str]]) -> str:
    buffer = ""
    for dialogue in chat_history:
        if isinstance(dialogue, tuple) and dialogue[0] in config["ROLES"]:
            identity = dialogue[0]
            message = dialogue[1]
            buffer += identity + ": " + message + "\n"
        else:
            raise ValueError(
                "Error loading the chat history. Possible causes: " + 
                f"Unsupported chat history format: {type(dialogue)}."
                f"Unsupported role: {dialogue[0]}."

                f" Full chat history: {chat_history} "
            )

    return buffer


class BaseSubMITChain(BaseConversationalRetrievalChain):
    """
    Chain for chatting with an index, specific for submit
    """
    retriever: BaseRetriever # Index to connect to
    max_tokens_limit: Optional[int] = 7000 # restrict doc length to return from store, enforced only for StuffDocumentChain
    get_chat_history: Optional[Callable[[List[Tuple[str, str]]], str]] = _get_chat_history


    # defined for compatibility with the BaseConversationalRetrievalChain, which expects _get_docs and _aget_docs in its __call__
    def _get_docs(self, question: str, inputs: Dict[str, Any]) -> List[Document]:

        docs = self.retriever.invoke(question)

        token_limiter = TokenLimiter(
            llm=self.combine_docs_chain.llm_chain.llm,
            max_tokens=self.max_tokens_limit
        )

        reduced_docs = token_limiter.reduce_tokens_below_limit(docs)
        
        return reduced_docs


    async def _aget_docs(self, question: str, inputs: Dict[str, Any]) -> List[Document]:
        docs = await self.retriever.ainvoke(question)

        token_limiter = TokenLimiter(
            llm=self.combine_docs_chain.llm_chain.llm,
            max_tokens=self.max_tokens_limit
        )

        reduced_docs = token_limiter.reduce_tokens_below_limit(docs)

        return reduced_docs


    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel,
        retriever: BaseRetriever,
        qa_prompt: BasePromptTemplate,
        condense_question_prompt: BasePromptTemplate,
        chain_type: str = "stuff",
        verbose: bool = False,
        condense_question_llm: Optional[BaseLanguageModel] = None,
        combine_docs_chain_kwargs: Optional[Dict] = None,
        **kwargs: Any,
    ) -> BaseConversationalRetrievalChain:
        # Load chain from LLM
        combine_docs_chain_kwargs = combine_docs_chain_kwargs or {}
        _prompt = qa_prompt
        document_variable_name = "context"

        #Add logger for storing input to the QA chain, ie filled QA template 
        logfile = os.path.join(data_path, config["logging"]["input_output_filename"])
        logger.add(logfile, colorize=True, enqueue=True)
        handler = FileCallbackHandler(logfile)

        llm_chain = LLMChain(
            llm=llm,
            prompt=_prompt,
            callbacks = [handler],
            verbose=verbose,
        )
        doc_chain = StuffDocumentsChain(
            llm_chain=llm_chain,
            document_variable_name=document_variable_name,
            callbacks = [handler],
            verbose=verbose)

        _llm = condense_question_llm or llm
        condense_question_chain = LLMChain(
            llm=_llm,
            prompt=condense_question_prompt,
            callbacks = [handler],
            verbose=verbose,
            output_key="question"
        )

        return cls(
            retriever=retriever,
            combine_docs_chain=doc_chain,
            question_generator=condense_question_chain,
            **kwargs,
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
        
        print(f"[BaseImageProcessingChain] Processing {len(images)} images.")
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
        logger.add(logfile, colorize=True, enqueue=True)
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
            print("Summary prompt, and thus chain, is not defined. Skipping summary step.")
        else:
            summary = self.summary_chain.run(
                submission_text=submission_text,
            )

        retrieved_docs = self.retriever.invoke(submission_text) if self.retriever else [] # "invoke" because get_relevant_documents is deprecated

        token_limiter = TokenLimiter(
            llm=self.final_grade_chain.llm,
            max_tokens=self.final_grade_chain.llm.max_tokens if hasattr(self.final_grade_chain.llm, 'max_tokens') else 7000,
            reserved_tokens=self._estimate_grader_reserved_tokens(submission_text, rubric_text, summary if self.summary_chain else "", additional_comments)
        )

        reduced_docs = token_limiter.reduce_tokens_below_limit(retrieved_docs)


        if reduced_docs:
            retrieved_context = "\n\n".join(doc.page_content for doc in reduced_docs)

        if not self.analysis_chain:
            print("Analysis prompt, and thus chain, is not defined. Skipping analysis step.")
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

        print(f"[BaseGradingChain] Estimated reserved tokens: {reserved_tokens}")

        return reserved_tokens