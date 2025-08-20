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
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger
from a2rchi.chains.utils.callback_handlers import PromptLogger
from a2rchi.chains.prompts import PROMPTS
from a2rchi.chains.models import print_model_params
from a2rchi.chains.retrievers import SubMITRetriever, GradingRetriever
from a2rchi.chains.utils import history_utils

logger = get_logger(__name__)

# DEFINITIONS
config = load_config()["chains"]["base"]
data_path = load_config()["global"]["DATA_PATH"]

class ChainWrapper:
    """
    Generic wrapper around Langchain's chains
    to harmonize with our prompts and inputs.
    """

    def __init__(
            self,
            chain: Any,
            llm: BaseLanguageModel,
            prompt: BasePromptTemplate,
            required_input_variables: List[str] = ['question'],
            unprunable_input_variables: Optional[List[str]] = [],
        ):
        self.chain = chain
        self.llm = llm
        self.required_input_variables = required_input_variables
        self.unprunable_input_variables = unprunable_input_variables
        self.prompt = self._check_prompt(prompt)

        self.prompt_logger = PromptLogger(os.path.join(data_path, config["logging"]["input_output_filename"]))
        self.token_limiter = TokenLimiter(llm=self.llm, prompt=self.prompt)

    def _check_prompt(self, prompt: BasePromptTemplate) -> BasePromptTemplate:
        """
        Check that the prompt is valid for this chain:
            1. require that it contains all the required input variables
        """
        for var in self.required_input_variables:
            if var not in prompt.input_variables:
                raise ValueError(f"Chain requires input variable {var} in the prompt, but could not find it.")
        return prompt
    
    def _prepare_payload(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare the input_variables to be passed to the chain.
        """

        # grab all the input variables from the parameters given to the function
        input_variables = {k:v for k,v in inputs.items() if k in self.prompt.input_variables}

        # reduce number of tokens, if necessary
        input_variables = self.token_limiter.prune_inputs_to_token_limit(**input_variables)

        return input_variables

    def invoke(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the chain to produce the LLM answer with some given inputs determined by the prompt.
        """

        # check if any of the unprunables are too large
        for var in self.unprunable_input_variables:
            if not self.token_limiter.check_input_size(inputs.get(var, "")):
                return {"answer": self.token_limiter.INPUT_SIZE_WARNING.format(var=var)}

        # get the payload
        input_variables = self._prepare_payload(inputs)
        
        # produce LLM response
        answer = self.chain.invoke(
            input_variables,
            config={"callbacks": [self.prompt_logger]}
        )

        return {"answer": answer, **input_variables}


class QAWorkflow:
    """
    Simple question and answer workflow with document retrieval.
    """

    def __init__(
        self,
        *args,
        **kwargs
    ):
        self.config = load_config(map=True)

        self.chain_config = self.config["chains"]["chain"]
        self.utils_config = self.config["utils"]

        # grab prompts
        self.qa_prompt = PROMPTS["MAIN_PROMPT"]
        self.condense_prompt = PROMPTS["CONDENSING_PROMPT"]

        # grab models
        model_class_map = self.chain_config["MODEL_CLASS_MAP"]
        model_name = self.chain_config.get("MODEL_NAME", None)
        condense_model_name = self.chain_config.get("CONDENSE_MODEL_NAME", model_name)
        self.llm = model_class_map[model_name]["class"](**model_class_map[model_name]["kwargs"])
        if condense_model_name == model_name:
            self.condense_llm = self.llm
        else:
            self.condense_llm = model_class_map[condense_model_name]["class"](**model_class_map[condense_model_name]["kwargs"])
        print_model_params("qa", model_name, model_class_map)
        print_model_params("condense", condense_model_name, model_class_map)

        # initialize chains
        self.condense_chain = ChainWrapper(
            chain=self.condense_prompt | self.llm | StrOutputParser(),
            llm=self.condense_llm,
            prompt=self.condense_prompt,
            required_input_variables=['history']
        )
        self.answer_chain = ChainWrapper(
            chain = create_stuff_documents_chain(
                llm=self.llm,
                prompt=self.qa_prompt,
                document_variable_name="retriever_output",
            ),
            llm=self.llm,
            prompt=self.qa_prompt,
            required_input_variables=['question'],
            unprunable_input_variables=['question']
        )

    def _prepare_inputs(history, **kwargs) -> Dict[str, Any]:
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
        self.retriever = SubMITRetriever(
            vectorstore=vectorstore,
            search_kwargs={"k": self.utils_config["data_manager"]["num_documents_to_retrieve"]},
        )

    def invoke(self, history, *args, **kwargs) -> Dict[str, Any]:
        """
        Execute the Workflow.
        """

        inputs = self._prepare_inputs(history)

        condense_output = self.condense_chain.invoke({
            **inputs
        })
        retriever_output = self.retriever.invoke(condense_output)
        answer_output = self.answer_chain.invoke({
            **inputs,
            'condense_output': condense_output['answer'],
            'retriever_output': retriever_output
        })

        return {
            "answer": answer_output['answer'],
            "documents": retriever_output,
            "condense_output": condense_output['answer']
        }

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
    

