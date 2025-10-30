from abc import abstractmethod
from typing import Any, Dict, List, Optional, Union

from langchain_core.caches import BaseCache
from langchain_core.language_models.llms import LLM

from src.utils.logging import get_logger

logger = get_logger(__name__)


def print_model_params(name: str, model_name: str, model_class_map: Dict[str, Dict[str, Any]]) -> None:
    """
    Print the parameters of the model.

    Parameters:
    name (str): The name of the model instance.
    model_name (str): The name of the model class.
    model_class_map (dict): The mapping of model class names to their classes and parameters.
    """
    params_str = "\n".join(
        [f"\t\t\t{param}: {value}" for param, value in model_class_map[model_name]["kwargs"].items()]
    )
    logger.info(f"Using {name} model {model_name} with parameters:\n{params_str}")


class BaseCustomLLM(LLM):
    """
    Abstract class used to load a custom LLM.
    """

    n_tokens: int = 100  # this has to be here for parent LLM class
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
    def _call(self, prompt: str, stop: Optional[List[str]] = None) -> str:
        pass
