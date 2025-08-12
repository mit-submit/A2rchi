import os
from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import numpy as np
import torch
import time

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.llms import LLM
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.caches import BaseCache 
from langchain_core.callbacks import Callbacks 

from qwen_vl_utils import process_vision_info

import requests
from typing import Optional, List

from a2rchi.chains.utils.prompt_formatters import PromptFormatter
from a2rchi.chains.utils.safety_checker import check_safety
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class BaseCustomLLM(LLM):
    """
    Abstract class used to load a custom LLM
    """
    n_tokens: int = 100 # this has to be here for parent LLM class
    cache: Union[BaseCache, bool, None] = None

    @property
    def _llm_type(self) -> str:
        return "custom"

    @classmethod
    def get_cached_model(cls, key):
        return cls._MODEL_CACHE.get(key)

    @classmethod
    def set_cached_model(cls, key, value):
        cls._MODEL_CACHE[key] = value

    @abstractmethod
    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        pass


class DumbLLM(BaseCustomLLM):
    """
    A simple Dumb LLM, perfect for testing
    """
    filler: str = None
    sleep_time_mean: int = 3

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        sleep_time = np.random.normal(self.sleep_time_mean, 1)
        logger.info(f"DumbLLM: sleeping {sleep_time}")
        time.sleep(sleep_time)
        return "I am just a dumb LLM, I will give you a number: " + str(np.random.randint(10000, 99999))


class AnthropicLLM(ChatAnthropic):
    """
    Loading Anthropic model from langchain package and specifying version. Options include:
        model: str = "claude-3-opus-20240229"
        model: str = "claude-3-sonnet-20240229"
    Model comparison: https://docs.anthropic.com/en/docs/about-claude/models#model-comparison 
    """

    model_name: str = "claude-3-opus-20240229"
    temp: int = 1




class LlamaLLM(BaseCustomLLM):
    """
    Loading the Llama LLM from facebook. Make sure that the model
    is downloaded and the base_model_path is linked to correct model
    """
    base_model: str = None     # location of the model (ex. meta-llama/Llama-2-70b)
    peft_model: str = None          # location of the finetuning of the model 
    enable_salesforce_content_safety: bool = True
                                    # enable safety check with Salesforce safety flan t5
    quantization: bool = True       # enables 8-bit quantization
    max_new_tokens: int = 2048      # maximum numbers of tokens to generate
    seed: int = None                # seed value for reproducibility
    do_sample: bool = True          # use sampling; otherwise greedy decoding
    min_length: int = None          # minimum length of sequence to generate, input prompt + min_new_tokens
    use_cache: bool = True          # [optional] model uses past last key/values attentions
    top_p: float = .9               # [optional] for float < 1, only smallest set of most probable tokens with prob. that add up to top_p or higher are kept for generation
    temperature: float = .6         # [optional] value used to modulate next token probs
    top_k: int = 50                 # [optional] number of highest prob. vocabulary tokens to keep for top-k-filtering
    repetition_penalty: float = 1.0 # parameter for repetition penalty: 1.0 == no penalty
    length_penalty: int = 1         # [optional] exponential penalty to length used with beam-based generation
    max_padding_length: int = None  # the max padding length used with tokenizer padding prompts

    tokenizer: Callable = None
    llama_model: Callable = None
    safety_checkers: List = None

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

        #Packages needed
        from peft import PeftModel
        from transformers import LlamaForCausalLM, LlamaTokenizer

         # Set the seeds for reproducibility
        if self.seed:
            torch.cuda.manual_seed(self.seed)
            torch.manual_seed(self.seed)

        # create tokenizer
        self.tokenizer = None
        self.tokenizer = LlamaTokenizer.from_pretrained(pretrained_model_name_or_path=self.base_model, local_files_only= False)
        # removed "load_in_8bit" argument, need to install correct bitsandbytes version then use BitsAndBytesConfig object and pass that
        base_model = LlamaForCausalLM.from_pretrained(pretrained_model_name_or_path=self.base_model, local_files_only= False, device_map='auto', torch_dtype = torch.float16, safetensors=True)
        if self.peft_model:
            self.llama_model = PeftModel.from_pretrained(base_model, self.peft_model, safetensors=True)
        else:
            self.llama_model = base_model
        self.llama_model.eval()

        # create safety checker
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
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        
        safe, safe_msg = check_safety(prompt, self.safety_checkers, 'prompt')
        if not safe:
            return safe_msg
        
        # prepare input
        batch = self.tokenizer(["[INST]" + prompt + "[/INST]"], padding='max_length', truncation=True,max_length=self.max_padding_length,return_tensors="pt")
        batch = {k: v.to("cuda") for k, v in batch.items()}

        # perform inference
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

        safe, safe_msg = check_safety(output_text, self.safety_checkers, 'output')
        if not safe:
            return safe_msg

        return output_text[output_text.rfind("[/INST]") + len("[/INST]"):]

class VLLM(BaseCustomLLM):
    """
    Loading a vLLM Model using the vllm Python package.
    Make sure the vllm package is installed and the model is available locally or remotely.
    Caveat: so far an older version 0.8.5 is used, thus older version of packadges are used, requirements_VLLN8.txt
    The newer version has introduced a bug in the VLLM, leading to errors:  TypeError: XFormersImpl.__init__() got an unexpected keyword argument 'layer_idx'
    """
    base_model: str = 'Qwen/Qwen2.5-7B-Instruct-1M'    # Model name or path (e.g., "meta-llama/Llama-2-7b-hf")
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 50
    repetition_penalty: float = 1.5
    seed: int = None
    max_new_tokens: int = 2048      # maximum numbers of tokens to generate
    enable_salesforce_content_safety: bool = False

    gpu_memory_utilization: float = 0.7
    tensor_parallel_size: int = 1
    trust_remote_code: bool = True
    tokenizer_mode: str = "auto"
    max_model_len: Optional[int] = None
    length_penalty: int = 1 

    vllm_engine: object = None
    #tokenizer: Optional[Any] = None

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

        from vllm import LLM as vllmLLM
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        if self.seed is not None:
            torch.manual_seed(self.seed)

        # set some environment variables to avoid issues
        os.environ['MKL_THREADING_LAYER']='GNU'
        os.environ['MKL_SERVICE_FORCE_INTEL']='1'
        os.environ["VLLM_DEFAULT_DTYPE"] = "float16"

        # create tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model, local_files_only=False)
        self.formatter = PromptFormatter(self.tokenizer, strip_html=True)

        model_cache_key = (self.base_model, "", "")
        cached = self.get_cached_model(model_cache_key)

        # create safety checker
        self.safety_checkers = []
        if self.enable_salesforce_content_safety:
            self.safety_checkers.append(SalesforceSafetyChecker())

        if cached:
            _, self.vllm_engine = cached
        else:
            # Load vLLM engine
            self.vllm_engine = vllmLLM(
                model=self.base_model,
                gpu_memory_utilization=self.gpu_memory_utilization,
                #trust_remote_code=self.trust_remote_code,
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
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        
        from vllm import SamplingParams

        safe, safe_msg = check_safety(prompt, self.safety_checkers, 'output')
        if not safe:
            return safe_msg

        formatted_prompt, end_tag = self.formatter.format_prompt(prompt)

        sampling_params = SamplingParams(
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            max_tokens=self.max_new_tokens,
            repetition_penalty=self.repetition_penalty,
            stop = stop
        )

        # vLLM expects a list of prompts
        outputs = self.vllm_engine.generate([formatted_prompt], sampling_params)
        # outputs is a list of RequestOutput, each has .outputs (list of generations)
        if outputs and outputs[0].outputs:
            safe, safe_msg = check_safety(outputs[0].outputs[0].text, self.safety_checkers, 'output')
            if not safe:
                return safe_msg
            return outputs[0].outputs[0].text
        else:
            return ""
        
VLLM._MODEL_CACHE = {}


class HuggingFaceOpenLLM(BaseCustomLLM):
    """
    Loading any chat-based LLM available on Hugging Face. Make sure that the model
    is downloaded and the base_model_path is linked to correct model.
    Pick your favorite: https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard#/
    Note you might need to change other parameters like max_new_tokens, prompt lengths, or other model specific parameters.
    """
    base_model: str = None     # location of the model (ex. meta-llama/Llama-2-70b)
    peft_model: str = None          # location of the finetuning of the model 
    enable_salesforce_content_safety: bool = False
                                    # enable safety check with Salesforce safety flan t5
    quantization: bool = False       # enables 8-bit quantization
    max_new_tokens: int = 1024      # maximum numbers of tokens to generate
    seed: int = None                # seed value for reproducibility
    do_sample: bool = True          # use sampling; otherwise greedy decoding
    min_length: int = None          # minimum length of sequence to generate, input prompt + min_new_tokens
    use_cache: bool = True          # [optional] model uses past last key/values attentions
    top_p: float = .9               # [optional] for float < 1, only smallest set of most probable tokens with prob. that add up to top_p or higher are kept for generation
    temperature: float = .6         # [optional] value used to modulate next token probs
    top_k: int = 50                 # [optional] number of highest prob. vocabulary tokens to keep for top-k-filtering
    repetition_penalty: float = 1.0 # parameter for repetition penalty: 1.0 == no penalty
    length_penalty: int = 1         # [optional] exponential penalty to length used with beam-based generation
    max_padding_length: int = None  # the max padding length used with tokenizer padding prompts

    tokenizer: Callable = None
    formatter: Callable = None
    hf_model: Callable = None
    safety_checkers: List = None

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

        #Packages needed
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        # Set the seeds for reproducibility
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
                    cache_dir="/root/models/", # load weights into dir mounted as volume so don't need to keepd downloading when iterating
                )
            else:
                base_model = AutoModelForCausalLM.from_pretrained(
                    self.base_model,
                    local_files_only=False,
                    device_map="auto",
                    torch_dtype=torch.float16,
                    use_safetensors=True,
                    cache_dir="/root/models/",  # load weights into dir mounted as volume so don't need to keep downloading when iterating
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
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        
        safe, safe_msg = check_safety(prompt, self.safety_checkers, 'prompt')
        if not safe:
            return safe_msg
        formatted_prompt, end_tag = self.formatter.format_prompt(prompt)

        batch = self.tokenizer(formatted_prompt, return_tensors="pt", add_special_tokens=False)
        batch = {k: v.to("cuda") for k, v in batch.items()}

        # perform inference
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

        safe, safe_msg = check_safety(output_text, self.safety_checkers, 'output')
        if not safe:
            return safe_msg
       
        return output_text[output_text.rfind(end_tag) + len(end_tag):]

# setting class-level cache for HuggingFaceOpenLLM to store loaded models
# prevents doubly loading the same model multiple times
HuggingFaceOpenLLM._MODEL_CACHE = {}


class HuggingFaceImageLLM(BaseCustomLLM):
    """
    Loading any image-based LLM available on Hugging Face. Make sure that the model
    is downloaded and the base_model_path is linked to correct model.
    Pick your favorite: https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard#/
    Note you might need to change other parameters, e.g., max_new_tokens.
    """
    base_model: str = None
    quantization: bool = False
    min_pixels: int = 224*28*28 # got these numbers from the HuggingFace page for Qwen2.5-VL-Chat-7B-Instruct... boh
    max_pixels: int = 1280*28*28
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

        #Packages needed
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoTokenizer, AutoProcessor
        

        # Set the seeds for reproducibility
        if self.seed:
            torch.manual_seed(self.seed)
            torch.cuda.manual_seed(self.seed)

        model_cache_key = (self.base_model, self.quantization, None)

        logger.debug(f"Cache is at: {id(HuggingFaceOpenLLM._MODEL_CACHE)}")
        logger.debug(f"Cache key: {model_cache_key} (base_model, quantization, peft_model)")
        logger.debug(f"Current keys: {list(HuggingFaceOpenLLM._MODEL_CACHE.keys())}")

        cached = self.get_cached_model(model_cache_key)
        if cached:
            logger.info("Model found in cache")
            self.processor, self.hf_model = cached
        else:
            logger.info("Model not in cache. Loading...")


        self.processor = AutoProcessor.from_pretrained(self.base_model, min_pixels=self.min_pixels, max_pixels=self.max_pixels, local_files_only=False)

        logger.debug(f"Processor type: {type(self.processor)}")
        logger.debug(f"Processor class name: {self.processor.__class__.__name__}")
        logger.debug(f"Has apply_chat_template: {hasattr(self.processor, 'apply_chat_template')}")
        import inspect
        if hasattr(self.processor, 'tokenizer'):
            sig = inspect.signature(self.processor.tokenizer.__call__)
            logger.debug(f"Tokenizer call parameters: {list(sig.parameters.keys())}")
            
        # Check processor's call signature
        sig = inspect.signature(self.processor.__call__)
        logger.debug(f"Processor call parameters: {list(sig.parameters.keys())}")
        
        self.hf_model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.base_model,
            device_map="auto",
            torch_dtype=torch.float16,
            local_files_only=False,
            use_safetensors=True,
            cache_dir="/root/models/",  # load weights into dir mounted as volume so don't need to keep downloading when iterating
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
        images: List[Union[str, Any]] = None, # base64 encoded images
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:

        logger.info(f"Processing prompt: {prompt}")

        # Single unified message format (like the official example)
        messages = [
            {
                "role": "user",
                "content": [
                    # Add images if they exist
                    *([{"type": "image", "image": f"data:image/jpeg;base64,{img}"} 
                    for img in images] if images else []),
                    # Add text
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        logger.info("Applying chat template")
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

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

        # perform inference
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



class OpenAILLM(ChatOpenAI):
    """
    Loading the various OpenAI models, most commonly

        model_name = 'gpt-4'
        model_name = 'gpt-3.5-turbo
    
    Make sure that the api key is loaded as an environment variable
    and the OpenAI package installed.
    """
    
    model_name: str = "gpt-4"
    temperature: int = 1


class SalesforceSafetyChecker():
    def __init__(self):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, AutoConfig
        import torch

        # load the salesforce safety model from Huggingface: https://huggingface.co/Salesforce/safety-flan-t5-base
        config = AutoConfig.from_pretrained("Salesforce/safety-flan-t5-base")
        self.tokenizer = AutoTokenizer.from_pretrained("Salesforce/safety-flan-t5-base") 
        self.model = AutoModelForSeq2SeqLM.from_pretrained("Salesforce/safety-flan-t5-base", config=config)
        self.model.eval()

    def __call__(self, output_text):

        prefix = "Is the <Text> field safe or unsafe?"
        input_ids = self.tokenizer(prefix + " <Text> " + output_text + " <Context> ", return_tensors="pt").input_ids
        
        if len(input_ids[0]) > 512:
            logger.warning("Input length is > 512 token. Safety check result could be incorrect.")

        with torch.no_grad():
            outputs = self.model.generate(input_ids,output_scores = True,return_dict_in_generate = True,max_new_tokens = 20)
        
        is_safe = self.tokenizer.decode(outputs.sequences[0], skip_special_tokens=True).split(" ")[0] == "safe"
        report = ""
        if not is_safe:
            true_false_ids = self.tokenizer("true false").input_ids[:2]
            keys = ["toxicity", "hate", "identity", "violence", "physical", "sexual", "profanity", "biased"]
            scores = {}
            for k, i in zip(keys, range(3,20,2)):
                scores[k] = round(outputs.scores[i][0,true_false_ids].softmax(dim=0)[0].item(), 5)
            
            report += "|" + "|".join(f"{n:^10}" for n in scores.keys()) + "|\n"
            report += "|" + "|".join(f"{n:^10}" for n in scores.values()) + "|\n"
        return "Salesforce Content Safety Flan T5 Base", is_safe, report



class ClaudeLLM(BaseCustomLLM):
    """
    An LLM class that uses Anthropic's Claude model.
    """
    #TODO: obscure api key in final production version
    api_key: str  = "INSERT KEY HERE!!!" # Claude API key
    base_url: str = "https://api.anthropic.com/v1/messages"  # Anthropic API endpoint
    model_name: str = "claude-3-5-sonnet-20240620"  # Specify the model version to use

    verbose: bool = False

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        max_tokens: int = 1024,
    ) -> str:

        if stop is not None:
            logger.warning("Currently this model does not support stop tokens")

        if self.verbose:
            logger.info(f"Starting call to Claude with prompt: {prompt}")

        headers = {
            "x-api-key": self.api_key,  # Use the API key for the x-api-key header
            "anthropic-version": "2023-06-01",  # Add the required version header
            "Content-Type": "application/json"
        }

        # Modify the payload to match the required structure
        payload = {
            "model": self.model_name,  # You can keep this dynamic based on your code
            "max_tokens": max_tokens,  # Update to match the required max_tokens
            "messages": [  # Use a list of messages where each message has a role and content
                {"role": "user", "content": prompt}  # Prompt becomes part of the message content
            ]
        }

        if self.verbose:
            logger.info("Sending request to Claude API")

        # Send request to Claude API
        response = requests.post(self.base_url, headers=headers, json=payload)

        if response.status_code == 200:
            completion = response.json()["content"][0]["text"]
            if self.verbose:
                logger.info(f"Received response from Claude API: {completion}")
            return completion
        else:
            raise Exception(f"API request to Claude failed with status {response.status_code}, {response.text}")
