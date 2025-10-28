from typing import Callable, List, Optional

import torch

from src.a2rchi.models.base import BaseCustomLLM
from src.a2rchi.models.safety import SalesforceSafetyChecker
from src.a2rchi.pipelines.classic_pipelines.utils.safety_checker import check_safety
from src.utils.logging import get_logger

logger = get_logger(__name__)


class LlamaLLM(BaseCustomLLM):
    """
    Loading the Llama LLM from facebook. Make sure that the model
    is downloaded and the base_model_path is linked to correct model.
    """

    base_model: str = None  # location of the model (ex. meta-llama/Llama-2-70b)
    peft_model: str = None  # location of the finetuning of the model
    enable_salesforce_content_safety: bool = True
    quantization: bool = True
    max_new_tokens: int = 2048
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
    llama_model: Callable = None
    safety_checkers: List = None

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

        from transformers import LlamaForCausalLM, LlamaTokenizer

        if self.seed:
            torch.cuda.manual_seed(self.seed)
            torch.manual_seed(self.seed)

        self.tokenizer = LlamaTokenizer.from_pretrained(
            pretrained_model_name_or_path=self.base_model, local_files_only=False
        )
        base_model = LlamaForCausalLM.from_pretrained(
            pretrained_model_name_or_path=self.base_model,
            local_files_only=False,
            device_map="auto",
            torch_dtype=torch.float16,
            safetensors=True,
        )
        if self.peft_model:
            from peft import PeftModel

            self.llama_model = PeftModel.from_pretrained(base_model, self.peft_model, safetensors=True)
        else:
            self.llama_model = base_model
        self.llama_model.eval()

        self.safety_checkers = []
        if self.enable_salesforce_content_safety:
            self.safety_checkers.append(SalesforceSafetyChecker())

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

        batch = self.tokenizer(
            ["[INST]" + prompt + "[/INST]"],
            padding="max_length",
            truncation=True,
            max_length=self.max_padding_length,
            return_tensors="pt",
        )
        batch = {k: v.to("cuda") for k, v in batch.items()}

        with torch.no_grad():
            outputs = self.llama_model.generate(
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

        output_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

        safe, safe_msg = check_safety(output_text, self.safety_checkers, "output")
        if not safe:
            return safe_msg

        return output_text[output_text.rfind("[/INST]") + len("[/INST]"):]
