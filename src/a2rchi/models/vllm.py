import os
from typing import Callable, List, Optional

import torch

from src.a2rchi.models.base import BaseCustomLLM
from src.a2rchi.models.safety import SalesforceSafetyChecker
from src.a2rchi.pipelines.classic_pipelines.utils.prompt_formatters import PromptFormatter
from src.a2rchi.pipelines.classic_pipelines.utils.safety_checker import check_safety
from src.utils.logging import get_logger

logger = get_logger(__name__)


class VLLM(BaseCustomLLM):
    """
    Loading a vLLM Model using the vllm Python package.
    Make sure the vllm package is installed and the model is available locally or remotely.
    Caveat: so far an older version 0.8.5 is used, thus older version of packadges are used, requirements_VLLN8.txt
    The newer version has introduced a bug in the VLLM, leading to errors:  TypeError: XFormersImpl.__init__() got an unexpected keyword argument 'layer_idx'
    """

    base_model: str = "Qwen/Qwen2.5-7B-Instruct-1M"
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 50
    repetition_penalty: float = 1.5
    seed: int = None
    max_new_tokens: int = 2048
    enable_salesforce_content_safety: bool = False

    gpu_memory_utilization: float = 0.7
    tensor_parallel_size: int = 1
    trust_remote_code: bool = True
    tokenizer_mode: str = "auto"
    max_model_len: Optional[int] = None
    length_penalty: int = 1

    vllm_engine: object = None
    tokenizer: Callable = None
    formatter: Callable = None
    hf_model: Callable = None
    safety_checkers: List = None

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

        try:
            import xformers

            logger.debug(f"xformers version: {xformers.__version__}")
        except ImportError:
            logger.debug("xformers is NOT installed.")

        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from vllm import LLM as vllmLLM

        if self.seed is not None:
            torch.manual_seed(self.seed)

        os.environ["MKL_THREADING_LAYER"] = "GNU"
        os.environ["MKL_SERVICE_FORCE_INTEL"] = "1"
        os.environ["VLLM_DEFAULT_DTYPE"] = "float16"

        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model, local_files_only=False)
        self.formatter = PromptFormatter(self.tokenizer, strip_html=True)

        model_cache_key = (self.base_model, "", "")
        cached = self.get_cached_model(model_cache_key)

        self.safety_checkers = []
        if self.enable_salesforce_content_safety:
            self.safety_checkers.append(SalesforceSafetyChecker())

        if cached:
            _, self.vllm_engine = cached
        else:
            self.vllm_engine = vllmLLM(
                model=self.base_model,
                gpu_memory_utilization=self.gpu_memory_utilization,
                tokenizer_mode=self.tokenizer_mode,
                tensor_parallel_size=self.tensor_parallel_size,
                dtype="float16",
                max_model_len=self.max_model_len,
            )
            self.set_cached_model(model_cache_key, (None, self.vllm_engine))

        logger.debug(f"Input nGPU={self.tensor_parallel_size}")

    @property
    def _llm_type(self) -> str:
        return "custom"

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
    ) -> str:
        from vllm import SamplingParams

        safe, safe_msg = check_safety(prompt, self.safety_checkers, "output")
        if not safe:
            return safe_msg

        formatted_prompt, _ = self.formatter.format_prompt(prompt)

        sampling_params = SamplingParams(
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            max_tokens=self.max_new_tokens,
            repetition_penalty=self.repetition_penalty,
            stop=stop,
        )

        outputs = self.vllm_engine.generate([formatted_prompt], sampling_params)
        if outputs and outputs[0].outputs:
            safe, safe_msg = check_safety(outputs[0].outputs[0].text, self.safety_checkers, "output")
            if not safe:
                return safe_msg
            return outputs[0].outputs[0].text
        return ""


VLLM._MODEL_CACHE = {}
