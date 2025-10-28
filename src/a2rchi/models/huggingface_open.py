from typing import Callable, List, Optional

import torch

from src.a2rchi.models.base import BaseCustomLLM
from src.a2rchi.models.safety import SalesforceSafetyChecker
from src.a2rchi.pipelines.classic_pipelines.utils.prompt_formatters import PromptFormatter
from src.a2rchi.pipelines.classic_pipelines.utils.safety_checker import check_safety
from src.utils.logging import get_logger

logger = get_logger(__name__)


class HuggingFaceOpenLLM(BaseCustomLLM):
    """
    Loading any chat-based LLM available on Hugging Face. Make sure that the model
    is downloaded and the base_model_path is linked to correct model.
    Pick your favorite: https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard#/
    Note you might need to change other parameters like max_new_tokens, prompt lengths, or other model specific parameters.
    """

    base_model: str = None
    peft_model: str = None
    enable_salesforce_content_safety: bool = False
    quantization: bool = False
    max_new_tokens: int = 1024
    seed: int = None
    do_sample: bool = True
    min_length: int = None
    use_cache: bool = True
    top_p: float = 0.9
    temperature: float = 0.6
    top_k: int = 50
    repetition_penalty: float = 1.0
    length_penalty: int = 1
    max_padding_length: int = None

    tokenizer: Callable = None
    formatter: Callable = None
    hf_model: Callable = None
    safety_checkers: List = None

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        if self.peft_model:
            from peft import PeftModel

        if self.seed:
            torch.manual_seed(self.seed)
            torch.cuda.manual_seed(self.seed)

        model_cache_key = (self.base_model, self.quantization, self.peft_model)

        logger.debug(f"Cache is at: {id(HuggingFaceOpenLLM._MODEL_CACHE)}")
        logger.debug(f"Cache key: {model_cache_key} (base_model, quantization, peft_model)")
        logger.debug(f"Current keys: {list(HuggingFaceOpenLLM._MODEL_CACHE.keys())}")

        cached = self.get_cached_model(model_cache_key)
        if cached:
            logger.info("Model and tokenizer found in cache")
            self.tokenizer, self.hf_model = cached
        else:
            logger.info("Model and tokenizer not in cache. Loading...")

            self.tokenizer = AutoTokenizer.from_pretrained(self.base_model, local_files_only=False)
            logger.info("Tokenizer loaded.")
            if self.quantization:
                bnbconfig = BitsAndBytesConfig(load_in_8bit=True)
                base_model = AutoModelForCausalLM.from_pretrained(
                    self.base_model,
                    local_files_only=False,
                    device_map="auto",
                    quantization_config=bnbconfig,
                    use_safetensors=True,
                    cache_dir="/root/models/",
                )
            else:
                base_model = AutoModelForCausalLM.from_pretrained(
                    self.base_model,
                    local_files_only=False,
                    device_map="auto",
                    torch_dtype=torch.float16,
                    use_safetensors=True,
                    cache_dir="/root/models/",
                )

            logger.info("Base model loaded.")

            if self.peft_model:
                self.hf_model = PeftModel.from_pretrained(base_model, self.peft_model)
            else:
                self.hf_model = base_model

            self.hf_model.eval()

            self.set_cached_model(model_cache_key, (self.tokenizer, self.hf_model))
            logger.info("Model loaded and cached.")

        self.safety_checkers = []
        if self.enable_salesforce_content_safety:
            logger.info("Salesforce safety checker enabled.")
            self.safety_checkers.append(SalesforceSafetyChecker())

        self.formatter = PromptFormatter(self.tokenizer)

    @property
    def _llm_type(self) -> str:
        return "custom"

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
    ) -> str:
        safe, safe_msg = check_safety(prompt, self.safety_checkers, "prompt")
        if not safe:
            return safe_msg
        formatted_prompt, end_tag = self.formatter.format_prompt(prompt)

        batch = self.tokenizer(formatted_prompt, return_tensors="pt", add_special_tokens=False)
        batch = {k: v.to("cuda") for k, v in batch.items()}

        with torch.no_grad():
            outputs = self.hf_model.generate(
                **batch,
                max_new_tokens=self.max_new_tokens,
                do_sample=self.do_sample,
                top_p=self.top_p,
                temperature=self.temperature,
                min_length=self.min_length,
                use_cache=self.use_cache,
                top_k=self.top_k,
                repetition_penalty=self.repetition_penalty,
                length_penalty=self.length_penalty,
            )

        logger.info("Inference completed, decoding output")
        output_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

        safe, safe_msg = check_safety(output_text, self.safety_checkers, "output")
        if not safe:
            return safe_msg

        return output_text[output_text.rfind(end_tag) + len(end_tag):]


HuggingFaceOpenLLM._MODEL_CACHE = {}
