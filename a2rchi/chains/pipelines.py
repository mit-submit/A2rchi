"""Chain for chatting with a vector database."""
from __future__ import annotations
from langchain_core.callbacks.file import FileCallbackHandler
from langchain_core.language_models.base import BaseLanguageModel
from langchain.chains.combine_documents.stuff import create_stuff_documents_chain
from langchain.chains.llm import LLMChain # deprecated, should update
from langchain.callbacks.manager import CallbackManagerForChainRun
from langchain_core.output_parsers import StrOutputParser
from langchain_core.retrievers import BaseRetriever
from langchain_core.prompts.base import BasePromptTemplate
from typing import Any, Dict, List, Optional
import os 

from a2rchi.chains.utils.token_limiter import TokenLimiter
from a2rchi.utils.logging import get_logger
from a2rchi.chains.models import print_model_params
from a2rchi.chains.retrievers import SemanticRetriever, GradingRetriever
from a2rchi.chains.utils import history_utils
from a2rchi.chains.chain_wrappers import ChainWrapper
from a2rchi.chains.utils.prompt_validator import ValidatedPromptTemplate
from a2rchi.chains.utils.prompt_utils import read_prompt

logger = get_logger(__name__)

class BasePipeline:
    """
    Basic structure of a Pipeline.

    Methods:
    --------
    update_retriever(vectorstore)
        Updates the retriever with a new vectorstore object

    invoke()
        Calls on the Pipeline with an user query, and other objects
        like chat history, images, etc. as per your Pipeline inputs.

    Returns:
    --------
    answer : str
        Answer to the user query.
    documents : list
        List of relevant sources to the user query.

    Examples:
    >>> pipeline = BasePipeline()
    >>> answer, docs = pipeline.invoke("What do we hold of the past?")

    """

    def __init__(
        self,
        config,
        *args,
        **kwargs
    ):
        self.config = config

    def update_retriever(self, vectorstore):
        self.retriever = None

    def invoke(self, *args, **kwargs)  -> Dict[str, Any]:
        return {
            "answer": "Stat rosa pristina nomine, nomina nuda tenemus.",
            "documents": []
        }

class QAPipeline(BasePipeline):
    """
    Simple question and answer Pipeline with document retrieval.

    Graph:
    (question) -> (condense prompt) -> (condense LLM) -> (condensed output)
    (condensed_output) -> (retriever) -> (documents)
    (documents, question, ...) -> (QA LLM) -> (answer)

    Returns:
    answer: LLM answer to question
    documents: retrieved documents
    condensed_output: LLM answer to condense prompt
    """

    def __init__(
        self,
        config,
        *args,
        **kwargs
    ):

        self.config = config
        self.a2rchi_config = self.config["a2rchi"]
        self.pipeline_config = self.a2rchi_config['pipeline_map']['QAPipeline']
        self.dm_config = self.config["data_manager"]

        # initialize prompts
        self.prompts = {
            name: ValidatedPromptTemplate(
                name=name,
                prompt_template=read_prompt(path),
            )
            for name, path in self.pipeline_config['prompts'].items() if path != ""
        }

        # initialize models
        self._init_llms()

        # initialize chains
        self.condense_chain = ChainWrapper(
            chain=self.prompts['condense_prompt'] | self.llm | StrOutputParser(),
            llm=self.condense_llm,
            prompt=self.prompts['condense_prompt'],
            required_input_variables=['history'],
            max_tokens=self.pipeline_config['max_tokens']
        )
        self.chat_chain = ChainWrapper(
            chain = create_stuff_documents_chain(
                llm=self.llm,
                prompt=self.prompts['chat_prompt'],
                document_variable_name="retriever_output",
            ),
            llm=self.llm,
            prompt=self.prompts['chat_prompt'],
            required_input_variables=['question'],
            unprunable_input_variables=['question'],
            max_tokens=self.pipeline_config['max_tokens']
        )

    def _init_llms(self):
        """
        Initalize LLM models from the config.
        """
        model_class_map = self.a2rchi_config["model_class_map"]
        chat_model = self.pipeline_config['models']['chat_model']
        condense_model = self.pipeline_config['models'].get("condense_model", chat_model)
        self.llm = model_class_map[chat_model]["class"](
            **model_class_map[chat_model]["kwargs"]
        )
        if condense_model == chat_model:
            self.condense_llm = self.llm
        else:
            self.condense_llm = model_class_map[condense_model]["class"](
                **model_class_map[condense_model]["kwargs"]
            )
        print_model_params("qa", chat_model, model_class_map)
        print_model_params("condense", condense_model, model_class_map)

    def _prepare_inputs(self, history, **kwargs) -> Dict[str, Any]:
        """
        Prepare inputs to be processed.
        We feed all inputs to all the chains, and each prompt handles
        what gets actually passed to the LLM to allow the user more
        flexibility over the inputs.
        All the inputs must be one of prompt_validator's SUPPORTED_INPUT_VARIABLES.
        """
        
        # seperate out the history into past interaction and current question input
        full_history = history_utils.tuplize_history(history)
        if len(full_history) > 0 and len(full_history[-1]) > 1:
            question = full_history[-1][1]
        else:
            logger.error("No question found")
            question = ""
        history = full_history[:-1] if full_history is not None else None

        return {
            "question": question,
            "history": history,
            "full_history": full_history
        }

    def update_retriever(self, vectorstore):
        """
        Update the retriever with a new vectorstore.
        """
        self.retriever = SemanticRetriever(
            vectorstore=vectorstore,
            search_kwargs={
                "k": self.dm_config["num_documents_to_retrieve"]
            },
            dm_config=self.dm_config
        )

    def invoke(self, **kwargs) -> Dict[str, Any]:
        """
        Execute the Pipeline.
        """

        # update connection to database
        vs = kwargs.get("vectorstore")
        if vs: self.update_retriever(vs) 

        # prepare inputs
        inputs = self._prepare_inputs(history=kwargs.get("history"))

        # execute our Pipeline
        condense_output = self.condense_chain.invoke({
            **inputs
        })
        retriever_output = self.retriever.invoke(
            condense_output['answer']
        )
        docs, scores = zip(*retriever_output)
        answer_output = self.chat_chain.invoke({
            **inputs,
            'condense_output': condense_output['answer'],
            'retriever_output': docs
        })

        return {
            "answer": answer_output['answer'],
            "documents": docs,
            "documents_scores": scores,
        }

# TODO put this in ChainWrappers
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

# TODO make this a Pipeline
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

# TODO make this a Pipeline
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
    

