from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import numpy as np
import torch
import time

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.llms import LLM
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

import requests
from typing import Optional, List

class BaseCustomLLM(LLM):
    """
    Abstract class used to load a custom LLM
    """
    n_tokens: int = 100 # this has to be here for parent LLM class

    @property
    def _llm_type(self) -> str:
        return "custom"

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
        print(f"DumbLLM: sleeping {sleep_time}")
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
    safety_checker: List = None

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
        self.safety_checker = []
        if self.enable_salesforce_content_safety:
            self.safety_checker.append(SalesforceSafetyChecker())

    @property
    def _llm_type(self) -> str:
        return "custom"

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        
        # check if input is safe:
        safety_results = [check(prompt) for check in self.safety_checker]
        are_safe = all([r[1] for r in safety_results])
        if not are_safe:
            print("User prompt deemed unsafe.")
            for method, is_safe, report in safety_results:
                if not is_safe:
                    print(method)
                    print(report)
            print("Skipping the Llama2 inference as the prompt is not safe.")
            return """It looks as if your question may be unsafe. 
                    
                    This may be due to issues relating to toxicity, hate, identity, violence, physical tones, sexual tones, profanity, or biased questions.
                    
                    Please try to reformat your question."""

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

        # safety check of the model output
        safety_results = [check(output_text) for check in self.safety_checker]
        are_safe = all([r[1] for r in safety_results])
        if not are_safe:
            print("Model output deemed unsafe.")
            for method, is_safe, report in safety_results:
                if not is_safe:
                    print(method)
                    print(report)
            return """The response to your question may be unsafe.

                    This may be due to issues relating to toxicity, hate, identity, violence, physical tones, sexual tones, profanity, or biased questions.
            
                    There are two ways to solve this:
                        - generate the response
                        - reformat your question so that it does not prompt an unsafe response."""

        return output_text[output_text.rfind("[/INST]") + len("[/INST]"):]


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
    hf_model: Callable = None
    safety_checker: List = None

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
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        # Set the seeds for reproducibility
        if self.seed:
            torch.manual_seed(self.seed)
            torch.cuda.manual_seed(self.seed)

        model_cache_key = (self.base_model, self.quantization, self.peft_model)

        print("[HuggingfaceOpenLLM] cache is at:", id(HuggingFaceOpenLLM._MODEL_CACHE))
        print("[HuggingFaceOpenLLM] cache key:", model_cache_key, "(base_model, quantization, peft_model)")
        print("[HuggingFaceOpenLLM] current keys:", list(HuggingFaceOpenLLM._MODEL_CACHE.keys()))

        cached = self.get_cached_model(model_cache_key)
        if cached:
            print("[HuggingFaceOpenLLM] model found in cache.")
            self.tokenizer, self.hf_model = cached
        else:
            print("[HuggingFaceOpenLLM] model and tokenizer not in cache. Loading...")

            self.tokenizer = AutoTokenizer.from_pretrained(self.base_model, local_files_only=False)
            print("[HuggingFaceOpenLLM] tokenizer loaded.")
            if self.quantization:
                bnbconfig = BitsAndBytesConfig(load_in_8bit=True)
                base_model = AutoModelForCausalLM.from_pretrained(
                    self.base_model,
                    local_files_only=False,
                    device_map="auto",
                    quantization_config=bnbconfig,
                    use_safetensors=True,
                )
            else:
                base_model = AutoModelForCausalLM.from_pretrained(
                    self.base_model,
                    local_files_only=False,
                    device_map="auto",
                    torch_dtype=torch.float16,
                    use_safetensors=True,
                )
            
            print("[HuggingFaceOpenLLM] base model loaded.")

            if self.peft_model:
                self.hf_model = PeftModel.from_pretrained(base_model, self.peft_model)
            else:
                self.hf_model = base_model

            self.hf_model.eval()

            self.set_cached_model(model_cache_key, (self.tokenizer, self.hf_model))
            print("[HuggingFaceOpenLLM] model loaded and cached.")

        self.safety_checker = []
        if self.enable_salesforce_content_safety:
            print("[HuggingFaceOpenLLM] Salesforce safety checker enabled.")
            self.safety_checker.append(SalesforceSafetyChecker())


    @property
    def _llm_type(self) -> str:
        return "custom"


    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        
        # longer term, better solution probably needed - shouldn't rely on string parsing the prompts...

        context_start = prompt.find("Context:")
        question_start = prompt.rfind("Question:")

        # check if input is safe:
        safety_results = [check(prompt[question_start:]) for check in self.safety_checker]
        are_safe = all([r[1] for r in safety_results])
        if not are_safe:
            print("User prompt deemed unsafe.")
            for method, is_safe, report in safety_results:
                if not is_safe:
                    print(method)
                    print(report)
            print(f"Skipping the {self.hf_model} inference as the prompt is not safe.")
            return """It looks as if your question may be unsafe. 
                    
                    This may be due to issues relating to toxicity, hate, identity, violence, physical tones, sexual tones, profanity, or biased questions.
                    
                    Please try to reformat your question."""

        # prepare input
        special_tokens = self.tokenizer.special_tokens_map

        # force chat template for final QA
        if "<|im_start|>" not in special_tokens.get("additional_special_tokens", []):
            self.tokenizer.add_special_tokens({"additional_special_tokens": ["<|im_start|>", "<|im_end|>"]})

        # instructor template
        if "[INST]" in special_tokens.get("additional_special_tokens", []):
            print("INFO - using instructor template")
            formatted_chat = f"[INST] {prompt} [/INST]"
            end_tag = "[/INST]"

        # chat template
        elif "<|im_start|>" in special_tokens.get("additional_special_tokens", []) and context_start != -1 and question_start != -1:
            print("INFO - using chat template")
            real_context = prompt[context_start+len("Context:"):question_start]
            question_end = prompt.rfind("Helpful Answer:") if 'Helpful Answer:' in prompt else len(prompt)

            message = [
                {"role": "system", "content": prompt[:context_start]},
                {"role": "assistant", "content": f"Here is some useful context:\n {real_context}"},
                {"role": "user", "content": prompt[question_start+len("Question:"):question_end]},
            ]
            formatted_chat = self.tokenizer.apply_chat_template(message, tokenize=False, add_generation_prompt=True)
            end_tag = "assistant"

        # current template for history and follow-up condensing before final QA
        elif "Standalone question:" in prompt:
            # Handle standalone question format
            print("INFO - using standalone question template")
            prompt = prompt.replace("Standalone question:", "Return only the standalone question in the next line, formatted with 'FINAL QUESTION: ', followed by the question itself.")
            formatted_chat = prompt
            end_tag = "FINAL QUESTION: "

        # fallback to default if no template detected
        else:
            print("INFO - not able to detect template, will try without (debugging info below)")
            print("DEBUG - special tokens: ", self.tokenizer.special_tokens_map)
            print("DEBUG - prompt: ", prompt)
            formatted_chat = prompt
            end_tag = ""

        batch = self.tokenizer(formatted_chat, return_tensors="pt", add_special_tokens=False)
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
            
        print("INFO - inference completed, decoding output.")
        output_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

        # safety check of the model output
        safety_results = [check(output_text) for check in self.safety_checker]
        are_safe = all([r[1] for r in safety_results])
        if not are_safe:
            print("Model output deemed unsafe.")
            for method, is_safe, report in safety_results:
                if not is_safe:
                    print(method)
                    print(report)
            return """The response to your question may be unsafe.

                    This may be due to issues relating to toxicity, hate, identity, violence, physical tones, sexual tones, profanity, or biased questions.
            
                    There are two ways to solve this:
                        - generate the response
                        - reformat your question so that it does not prompt an unsafe response."""
       
        return output_text[output_text.rfind(end_tag) + len(end_tag):]

# setting class-level cache for HuggingFaceOpenLLM to store loaded models
# prevents doubly loading the same model multiple times
HuggingFaceOpenLLM._MODEL_CACHE = {}

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
            print("Input length is > 512 token. Safety check result could be incorrect.")

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




class BaseCustomLLM(LLM):
    """
    Abstract class used to load a custom LLM
    """
    n_tokens: int = 100 # this has to be here for parent LLM class

    @property
    def _llm_type(self) -> str:
        return "custom"

    @abstractmethod
    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        pass


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
            print("WARNING : currently this model does not support stop tokens")

        if self.verbose:
            print(f"INFO : Starting call to Claude with prompt: {prompt}")

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
            print("INFO: Sending request to Claude API")

        # Send request to Claude API
        response = requests.post(self.base_url, headers=headers, json=payload)

        if response.status_code == 200:
            completion = response.json()["content"][0]["text"]
            if self.verbose:
                print(f"INFO : received response from Claude API: {completion}")
            return completion
        else:
            raise Exception(f"API request to Claude failed with status {response.status_code}, {response.text}")
