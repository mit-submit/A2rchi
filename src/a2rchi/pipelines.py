"""Chain for chatting with a vector database."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from langchain.chains.combine_documents.stuff import \
    create_stuff_documents_chain
from langchain_core.callbacks.file import FileCallbackHandler
from langchain_core.language_models.base import BaseLanguageModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts.base import BasePromptTemplate
from langchain_core.retrievers import BaseRetriever

from src.a2rchi.chain_wrappers import ChainWrapper
from src.a2rchi.chains import ImageLLMChain
from src.a2rchi.models import print_model_params
from src.a2rchi.retrievers import GradingRetriever, SemanticRetriever
from src.a2rchi.utils import history_utils
from src.a2rchi.utils.prompt_utils import read_prompt
from src.a2rchi.utils.prompt_validator import ValidatedPromptTemplate
from src.a2rchi.utils.token_limiter import TokenLimiter
from src.utils.logging import get_logger

logger = get_logger(__name__)

class BasePipeline:
    """
    BasePipeline provides a foundational structure for building pipeline classes that process user queries using configurable language models and prompts.

    Attributes
    ----------
    config : dict
        Configuration dictionary containing pipeline, model, and data manager settings.
    a2rchi_config : dict
        Sub-configuration for A2rchi-specific settings.
    dm_config : dict
        Sub-configuration for data manager settings.
    pipeline_config : dict
        Pipeline-specific configuration extracted from a2rchi_config.
    llms : dict
        Dictionary mapping model names to initialized language model instances.
    prompts : dict
        Dictionary mapping prompt names to ValidatedPromptTemplate instances.

    Methods
    -------
    - __init__(config, *args, **kwargs)
        Initializes the pipeline with the provided configuration, setting up models and prompts.
        Updates the pipeline's retriever with a new vectorstore object.
    - invoke(*args, **kwargs) -> Dict[str, Any]
        Processes a user query and returns an answer and relevant documents.
    - _init_llms()
        Initializes language model instances as specified in the configuration.
    - _init_prompts()
        Loads and validates prompt templates from the configuration.

    Returns
    -------
    dict
        {
            "answer": str,                # LLM-generated answer to the query
            "documents": List[Document],  # List of relevant documents
        }

    Usage
    -----
    Instantiate BasePipeline with the required configuration, then call `invoke()` with the user query and (optionally) a vectorstore.

    Examples
    --------
    >>> pipeline = BasePipeline(config)
    >>> result = pipeline.invoke("What do we hold of the past?")
    >>> print(result["answer"])
    """

    def __init__(
        self,
        config,
        *args,
        **kwargs
    ):
        self.config = config
        self.a2rchi_config = self.config["a2rchi"]
        self.dm_config = self.config["data_manager"]
        self.pipeline_config = self.a2rchi_config['pipeline_map'][self.__class__.__name__]
        self._init_llms()
        self._init_prompts()

    def update_retriever(self, vectorstore):
        self.retriever = None

    def invoke(self, *args, **kwargs)  -> Dict[str, Any]:
        return {
            "answer": "Stat rosa pristina nomine, nomina nuda tenemus.",
            "documents": []
        }

    def _init_llms(self):
        """
        Initialize LLM models from the config.

        The config should look like:

        ```
            pipeline_map:
                <PipelineName>:
                    models:
                        required:
                            model_name: <ModelClassName>  # must be in model_class_map
                            ...
                        optional:
                            model_name: <ModelClassName>  # must be in model_class_map
                            ...
        ``

        If a ModelClass has already been initialized, copy it to another model_name.
        LLMs are initialized to a self.llms dictionary containing:

        ```
            self.llms = {
                "model_name": <ModelInstance>,
                ...
            }
        ```
        """

        model_class_map = self.a2rchi_config["model_class_map"]
        models_config = self.pipeline_config.get("models", {})
        self.llms = {}

        # Combine required and optional model configs
        all_models = dict(models_config.get("required", {}), **models_config.get("optional", {}))

        # Track already initialized model instances to avoid duplicates
        initialized_models = {}

        for model_name, model_class_name in all_models.items():
            if model_class_name in initialized_models:
                # Reuse the already initialized instance
                self.llms[model_name] = initialized_models[model_class_name]
                logger.debug(f"Reusing initialized model '{model_name}' of class '{model_class_name}'")
            else:
                model_class = model_class_map[model_class_name]["class"]
                model_kwargs = model_class_map[model_class_name]["kwargs"]
                instance = model_class(**model_kwargs)
                self.llms[model_name] = instance
                initialized_models[model_class_name] = instance

    def _init_prompts(self):
        """
        Initialize prompts from the config.

        The config should look like:

        ```
            pipeline_map:
                <PipelineName>:
                    prompts:
                        required:
                            prompt_name: path/to/prompt.txt
                            ...
                        optional:
                            prompt_name: path/to/prompt.txt
                            ...
        ```

        Prompts are initialized to a self.prompts dictionary containing:

        ```
            self.prompts = {
                "prompt_name": <ValidatedPromptTemplateInstance>,
                ...
            }
        ```
        """

        all_prompts = dict(
            self.pipeline_config.get("prompts", {}).get("required", {}) | self.pipeline_config.get("prompts", {}).get("optional", {})
        )
        self.prompts = {}
        for name, path in all_prompts.items():
            try:
                if path:
                    prompt_template = read_prompt(path)
                    self.prompts[name] = ValidatedPromptTemplate(
                        name=name,
                        prompt_template=prompt_template,
                    )
            except FileNotFoundError as e:
                if name in self.pipeline_config.get("prompts", {}).get("required", {}):
                    raise FileNotFoundError(f"Required prompt file '{path}' for '{name}' not found: {e}")
                else:
                    logger.warning(f"Optional prompt file '{path}' for '{name}' not found or unreadable: {e}")
                    continue


class QAPipeline(BasePipeline):
    """
    QAPipeline is a modular pipeline for question answering (QA) tasks that integrates document retrieval and large language model (LLM) reasoning.

    Overview
    --------
    This pipeline processes a user question by first condensing the conversation history, retrieving relevant documents, and then generating an answer using the retrieved context.
    
    Pipeline Flow
    -------------
    1. (question) -> (condense prompt) -> (condense LLM) -> (condensed output)
    2. (condensed_output) -> (retriever) -> (documents)
    3. (documents, question, ...) -> (QA LLM) -> (answer)
    
    Key Components
    --------------
    - condense_chain: Summarizes or condenses the conversation history to focus the retrieval step.
    - retriever: Retrieves relevant documents from a vectorstore based on the condensed question.
    - chat_chain: Generates the final answer using the question and retrieved documents.

    Parameters
    ----------
    config : dict
        Configuration dictionary for the pipeline, including LLMs, prompts, and pipeline settings.
    *args, **kwargs
        Additional arguments passed to the BasePipeline.
    
    Methods
    -------
    - __init__(config, *args, **kwargs): Initializes the pipeline, LLMs, prompts, and chains.
    - _prepare_inputs(history, **kwargs): Prepares and formats inputs for the pipeline, extracting the current question and conversation history.
    - update_retriever(vectorstore): Updates the retriever with a new vectorstore for document retrieval.
    - invoke(**kwargs): Executes the pipeline end-to-end, returning the answer, retrieved documents, and their scores.
    
    Returns
    -------
    dict
        {
            "answer": str,                # LLM-generated answer to the question
            "documents": List[Document],  # List of retrieved documents
            "documents_scores": List[float], # Relevance scores for each document
        }

    Usage
    -----
    Instantiate QAPipeline with the required configuration, then call `invoke()` with the conversation history and (optionally) a vectorstore.
    
    Example
    -------
    >>> pipeline = QAPipeline(config)
    >>> result = pipeline.invoke(history=chat_history, vectorstore=my_vectorstore)
    >>> print(result["answer"])
    """

    def __init__(
        self,
        config,
        *args,
        **kwargs
    ):

        super().__init__(config, *args, **kwargs)

        # initialize chains
        self.condense_chain = ChainWrapper(
            chain=self.prompts['condense_prompt'] | self.llms['condense_model'] | StrOutputParser(),
            llm=self.llms['condense_model'],
            prompt=self.prompts['condense_prompt'],
            required_input_variables=['history'],
            max_tokens=self.pipeline_config['max_tokens']
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
            max_tokens=self.pipeline_config['max_tokens']
        )

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
        if retriever_output:
            docs, scores = zip(*retriever_output)
        else:
            docs, scores = [], []
        answer_output = self.chat_chain.invoke({
            **inputs,
            'condense_output': condense_output['answer'],
            'retriever_output': docs if docs else ""
        })

        return {
            "answer": answer_output['answer'],
            "documents": docs,
            "documents_scores": scores,
        }


class ImageProcessingPipeline(BasePipeline):
    """
    Simple pipeline for processing images.
    """

    def __init__(
            self,
            config,
            *args,
            **kwargs
    ):
        
        super().__init__(config, *args, **kwargs)

        self._image_llm_chain = ImageLLMChain(
            llm=self.llms['image_processing_model'],
            prompt=self.prompts['image_processing_prompt']
        )
        self.image_processing_chain = ChainWrapper(
            chain=self._image_llm_chain,
            llm=self.llms['image_processing_model'],
            prompt=self.prompts['image_processing_prompt'],
            required_input_variables=[],
            **kwargs,
        )

    def invoke(
        self,
        images: List[str, Any],
        **kwargs
    ) -> Dict[str, str]:
        """
        Run the image processing chain with the provided images.
        """
        
        logger.info(f"Processing {len(images)} images.")
        text_from_image = self.image_processing_chain.invoke(
            inputs={"images": images}
        )

        return text_from_image


class GradingPipeline(BasePipeline):
    """
    Pipeline for grading a response using summary, analysis, and final grade steps.
    """

    def __init__(
        self,
        config,
        *args,
        **kwargs
    ):
        super().__init__(config, *args, **kwargs)
        self.summary_chain = None
        self.analysis_chain = None
        self.final_grade_chain = None
        self.retriever = None
        self._init_chains()

    def _init_chains(self):
        # Initialize summary, analysis, and final grade chains if prompts are provided
        if 'summary_prompt' in self.prompts:
            self.summary_chain = ChainWrapper(
                chain=self.prompts['summary_prompt'] | self.llms['final_grade_model'] | StrOutputParser(),
                llm=self.llms['final_grade_model'],
                prompt=self.prompts['summary_prompt'],
                required_input_variables=['submission_text'],
                max_tokens=self.pipeline_config.get('max_tokens', 7000)
            )
        if 'analysis_prompt' in self.prompts:
            self.analysis_chain = ChainWrapper(
                chain=self.prompts['analysis_prompt'] | self.llms['analysis_model'] | StrOutputParser(),
                llm=self.llms['analysis_model'],
                prompt=self.prompts['analysis_prompt'],
                required_input_variables=['submission_text', 'rubric_text', 'summary'],
                max_tokens=self.pipeline_config.get('max_tokens', 7000)
            )
        self.final_grade_chain = ChainWrapper(
            chain=self.prompts['final_grade_prompt'] | self.llms['final_grade_model'] | StrOutputParser(),
            llm=self.llms['final_grade_model'],
            prompt=self.prompts['final_grade_prompt'],
            required_input_variables=['rubric_text', 'submission_text', 'analysis'],
            max_tokens=self.pipeline_config.get('max_tokens', 7000)
        )

    def update_retriever(self, vectorstore):
        self.retriever = SemanticRetriever(
            vectorstore=vectorstore,
            search_kwargs={
                "k": self.dm_config.get("num_documents_to_retrieve", 4)
            },
            dm_config=self.dm_config
        )

    def _estimate_grader_reserved_tokens(self, submission_text: str, rubric_text: str, summary: str, additional_comments: str) -> int:
        reserved_tokens = 300
        llm = self.llms['final_grade_model']
        reserved_tokens += llm.get_num_tokens(submission_text)
        reserved_tokens += llm.get_num_tokens(rubric_text)
        reserved_tokens += llm.get_num_tokens(summary)
        reserved_tokens += llm.get_num_tokens(additional_comments)
        logger.info(f"Estimated reserved tokens: {reserved_tokens}")
        return reserved_tokens

    def invoke(
        self,
        submission_text: str,
        rubric_text: str,
        additional_comments: str = "",
        vectorstore=None,
        **kwargs
    ) -> Dict[str, str]:
        """
        Run the grading pipeline with the provided submission text and rubric.
        """
        if vectorstore:
            self.update_retriever(vectorstore)

        summary = "No solution summary."
        if self.summary_chain:
            summary = self.summary_chain.invoke({
                "submission_text": submission_text
            })['answer']

        retrieved_docs = []
        if self.retriever:
            retrieved_docs, _ = zip(*self.retriever.invoke(submission_text)) if self.retriever.invoke(submission_text) else ([], [])

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

        return {
            "summary": summary,
            "analysis": analysis,
            "final_grade": final_grade,
            "retrieved_context": retrieved_docs
        }
