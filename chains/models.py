from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import numpy as np

from langchain.callbacks.manager import CallbackManagerForLLMRun
from langchain.llms.base import LLM
from langchain.chat_models import ChatOpenAI
from langchain.llms import LlamaCpp



class BaseCustomLLM(LLM):
    """
    Abstract class used to load a custom LLM
    """
    n_tokens: int = 100 #this has to be here for parent LLM class

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

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        return "I am just a dumb LLM who can't do anything except for give you a number: " + str(np.random.randint(10000, 99999))

class LlamaLLM(LlamaCpp):
    """
    Loading the Llama LLM from facebook. Make sure that the model
    is downloaded and the model_path is linked to with model_path
    """

    model_path: str = None

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