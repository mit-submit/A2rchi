import inspect
from typing import Any, Callable, List, Optional, Union

import torch
from qwen_vl_utils import process_vision_info

from src.a2rchi.models.base import BaseCustomLLM
from src.utils.logging import get_logger

logger = get_logger(__name__)


class HuggingFaceImageLLM(BaseCustomLLM):
    """
    Loading any image-based LLM available on Hugging Face. Make sure that the model
    is downloaded and the base_model_path is linked to correct model.
    Pick your favorite: https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard#/
    Note you might need to change other parameters, e.g., max_new_tokens.
    """

    base_model: str = None
    quantization: bool = False
    min_pixels: int = 224 * 28 * 28
    max_pixels: int = 1280 * 28 * 28
    max_new_tokens: int = 1024
    seed: int = None
    do_sample: bool = False
    min_length: int = None
    use_cache: bool = True
    top_k: int = 50
    repetition_penalty: float = 1.0
    length_penalty: int = 1
    processor: Callable = None
    hf_model: Callable = None

    @classmethod
    def get_cached_model(cls, key):
        return cls._MODEL_CACHE.get(key)

    @classmethod
    def set_cached_model(cls, key, value):
        cls._MODEL_CACHE[key] = value

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        if self.seed:
            torch.manual_seed(self.seed)
            torch.cuda.manual_seed(self.seed)

        model_cache_key = (self.base_model, self.quantization, None)

        logger.debug(f"Cache is at: {id(HuggingFaceImageLLM._MODEL_CACHE)}")
        logger.debug(f"Cache key: {model_cache_key} (base_model, quantization, peft_model)")
        logger.debug(f"Current keys: {list(HuggingFaceImageLLM._MODEL_CACHE.keys())}")

        cached = self.get_cached_model(model_cache_key)
        if cached:
            logger.info("Model found in cache")
            self.processor, self.hf_model = cached
        else:
            logger.info("Model not in cache. Loading...")

        self.processor = AutoProcessor.from_pretrained(
            self.base_model,
            min_pixels=self.min_pixels,
            max_pixels=self.max_pixels,
            local_files_only=False,
        )

        logger.debug(f"Processor type: {type(self.processor)}")
        logger.debug(f"Processor class name: {self.processor.__class__.__name__}")
        logger.debug(f"Has apply_chat_template: {hasattr(self.processor, 'apply_chat_template')}")
        if hasattr(self.processor, "tokenizer"):
            sig = inspect.signature(self.processor.tokenizer.__call__)
            logger.debug(f"Tokenizer call parameters: {list(sig.parameters.keys())}")

        sig = inspect.signature(self.processor.__call__)
        logger.debug(f"Processor call parameters: {list(sig.parameters.keys())}")

        self.hf_model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.base_model,
            device_map="auto",
            torch_dtype=torch.float16,
            local_files_only=False,
            use_safetensors=True,
            cache_dir="/root/models/",
        )

        self.hf_model.eval()

        self.set_cached_model(model_cache_key, (self.processor, self.hf_model))
        logger.info(f"{self.base_model} model loaded and cached.")

    @property
    def _llm_type(self) -> str:
        return "custom"

    def _call(
        self,
        prompt: str = None,
        images: List[Union[str, Any]] = None,
        stop: Optional[List[str]] = None,
    ) -> str:
        logger.info(f"Processing prompt: {prompt}")

        messages = [
            {
                "role": "user",
                "content": [
                    *(
                        [{"type": "image", "image": f"data:image/jpeg;base64,{img}"} for img in images]
                        if images
                        else []
                    ),
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        logger.info("Applying chat template")
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        logger.info("Processing vision info")
        image_inputs, video_inputs = process_vision_info(messages)

        logger.info("Tokenizing inputs")
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = inputs.to("cuda")

        logger.info("Performing inference")
        with torch.no_grad():
            generated_ids = self.hf_model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=self.do_sample,
                min_length=self.min_length,
                use_cache=self.use_cache,
                top_k=self.top_k,
                repetition_penalty=self.repetition_penalty,
                length_penalty=self.length_penalty,
            )

        logger.info("Decoding output")
        generated_ids_trimmed = [
            out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]

        output_text = self.processor.batch_decode(
            generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )

        return output_text


HuggingFaceImageLLM._MODEL_CACHE = {}
