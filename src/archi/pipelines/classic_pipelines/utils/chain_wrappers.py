import os
import pprint
from typing import Any, Dict, List, Optional

from langchain_core.language_models.base import BaseLanguageModel
from langchain_core.prompts.base import BasePromptTemplate
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.archi.pipelines.classic_pipelines.utils.token_limiter import TokenLimiter
from src.utils.logging import get_logger
from src.utils.config_access import get_global_config

logger = get_logger(__name__)


class _UsageCollector(BaseCallbackHandler):
    """LangChain callback that captures token usage and reasoning/thinking content from LLM responses."""

    def __init__(self):
        super().__init__()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.thinking_chunks: List[str] = []

    def on_llm_end(self, response: LLMResult, **kwargs):
        for gen_list in response.generations:
            for gen in gen_list:
                info = getattr(gen, "generation_info", None) or {}
                usage = info.get("usage") or info.get("token_usage") or {}
                # LangChain also puts usage in response.llm_output
                if not usage and hasattr(response, "llm_output") and response.llm_output:
                    usage = response.llm_output.get("usage") or response.llm_output.get("token_usage") or {}
                self.prompt_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                self.completion_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
                # Ollama format
                if not self.prompt_tokens and not self.completion_tokens:
                    msg = getattr(gen, "message", None)
                    if msg:
                        resp_meta = getattr(msg, "response_metadata", None) or {}
                        self.prompt_tokens += resp_meta.get("prompt_eval_count", 0)
                        self.completion_tokens += resp_meta.get("eval_count", 0)
                        # Also try usage_metadata on the message
                        usage_meta = getattr(msg, "usage_metadata", None)
                        if isinstance(usage_meta, dict) and not self.prompt_tokens:
                            self.prompt_tokens += usage_meta.get("input_tokens", 0)
                            self.completion_tokens += usage_meta.get("output_tokens", 0)
                # Reasoning/thinking content (langchain_ollama puts it in additional_kwargs["reasoning_content"]
                # when ChatOllama is constructed with reasoning=True).
                msg = getattr(gen, "message", None)
                if msg is not None:
                    additional = getattr(msg, "additional_kwargs", None) or {}
                    for key in ("reasoning_content", "thinking", "reasoning"):
                        v = additional.get(key)
                        if isinstance(v, str) and v.strip():
                            self.thinking_chunks.append(v)
                            break

    @property
    def usage(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }

    @property
    def thinking_content(self) -> str:
        return "\n\n".join(self.thinking_chunks) if self.thinking_chunks else ""

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
        global_configs = get_global_config()

        # reduce number of tokens, if necessary
        inputs = self.token_limiter.prune_inputs_to_token_limit(**inputs)

        # if there are variables asked for in the prompt that aren't passed, initialize to empty string
        for var in self.prompt.input_variables:
            if var not in inputs:
                logger.debug(f"Input variable '{var}' not provided, initializing to empty string.")
                inputs[var] = ""
        
        return inputs

    def invoke(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the chain to produce the LLM answer with some given inputs determined by the prompt.
        """
        logger.debug("Invoked chain with inputs:\n%s", pprint.pformat(inputs, indent=2))

        # check if any of the unprunables are too large
        for var in self.unprunable_input_variables:
            if not self.token_limiter.check_input_size(inputs.get(var, "")):
                return {"answer": self.token_limiter.INPUT_SIZE_WARNING.format(var=var), "usage": None}

        # get the payload
        input_variables = self._prepare_payload(inputs)

        logger.debug("Prepared input variables for chain:\n%s", pprint.pformat(input_variables, indent=2))
        
        # produce LLM response with usage tracking
        usage_collector = _UsageCollector()
        answer = self.chain.invoke(
            input_variables,
            config={"callbacks": [usage_collector]}
        )

        logger.debug(f"Chain produced answer: {answer}")

        return {
            "answer": answer,
            "usage": usage_collector.usage,
            "thinking_content": usage_collector.thinking_content,
            **input_variables,
        }
