from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import numpy as np
import torch

from langchain.callbacks.manager import CallbackManagerForLLMRun
from langchain.llms.base import LLM
from langchain.chat_models import ChatOpenAI
from langchain.llms import LlamaCpp

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

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        return "I am just a dumb LLM, I will give you a number: " + str(np.random.randint(10000, 99999))

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
    max_new_tokens: int = 4096      # maximum numbers of tokens to generate
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
        base_model = LlamaForCausalLM.from_pretrained(pretrained_model_name_or_path=self.base_model, local_files_only= False, load_in_8bit=self.quantization, device_map='auto', torch_dtype = torch.float16)
        if self.peft_model:
            self.llama_model = PeftModel.from_pretrained(base_model, self.peft_model)
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
