import os
from typing import Any, Dict, List, Optional
from langchain_core.language_models.base import BaseLanguageModel
from langchain_core.prompts.base import BasePromptTemplate
from langchain.chains.llm import LLMChain # deprecated, should update
from langchain.callbacks.manager import CallbackManagerForChainRun

from a2rchi.chains.utils.callback_handlers import PromptLogger
from a2rchi.chains.utils.token_limiter import TokenLimiter
from a2rchi.utils.config_loader import load_global_config
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

# DEFINITIONS
global_configs = load_global_config()

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
            max_tokens: int = 1e10
        ):
        self.chain = chain
        self.llm = llm
        self.required_input_variables = required_input_variables
        self.unprunable_input_variables = unprunable_input_variables
        self.prompt = self._check_prompt(prompt)

        self.prompt_logger = PromptLogger(
            os.path.join(global_configs["DATA_PATH"], global_configs["LOGGING"]["input_output_filename"])
        )
        self.token_limiter = TokenLimiter(
            llm=self.llm,
            prompt=self.prompt,
            max_tokens=max_tokens,
            unprunable_input_variables=unprunable_input_variables
        )

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

        # reduce number of tokens, if necessary
        inputs = self.token_limiter.prune_inputs_to_token_limit(**inputs)

        # if there are variables asked for in the prompt that aren't passed, initialize to empty string
        for var in self.prompt.input_variables:
            if var not in inputs:
                inputs[var] = ""
        
        return inputs

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