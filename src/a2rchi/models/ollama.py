from langchain_ollama.chat_models import ChatOllama

from src.utils.logging import get_logger

logger = get_logger(__name__)


class OllamaInterface(ChatOllama):
    """
    An LLM class that uses a model connected to an Ollama server interface.
    """

    model_name: str = ""
    url: str = ""

    def __init__(self, **kwargs):
        model_name = kwargs.pop("base_model", "")
        url = kwargs.pop("url", "")

        if url == "":
            logger.error("No base-url selected for Ollama model")

        if model_name == "":
            logger.error("No Ollama model selected.")

        super().__init__(model=model_name, base_url=url, **kwargs)
