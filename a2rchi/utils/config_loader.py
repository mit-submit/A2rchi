from a2rchi.chains.models import OpenAILLM, DumbLLM, LlamaLLM, AnthropicLLM, HuggingFaceOpenLLM, HuggingFaceImageLLM, VLLM

from langchain_openai import OpenAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings

import os
import yaml

# DEFINITIONS
CONFIG_PATH = "/root/A2rchi/config.yaml"

class Config_Loader:

    def __init__(self):
        self.config = self.load_config()

    def load_config(self):
        """
        Small function for loading the config.yaml file
        """
        # env = os.getenv("RUNTIME_ENV")
        # try:
        #     with open(f"./config/{env}-config.yaml", "r") as f:
        #         config = yaml.load(f, Loader=yaml.FullLoader)
        try:
            with open(CONFIG_PATH, "r") as f:
                config = yaml.load(f, Loader=yaml.FullLoader)

            # change the model class parameter from a string to an actual class
            MODEL_MAPPING = {
                "AnthropicLLM": AnthropicLLM,
                "OpenAIGPT4": OpenAILLM,
                "OpenAIGPT35": OpenAILLM,
                "DumbLLM": DumbLLM,
                "LlamaLLM": LlamaLLM,
                "HuggingFaceOpenLLM": HuggingFaceOpenLLM,
                "HuggingFaceImageLLM": HuggingFaceImageLLM,
                "VLLM": VLLM,
            }
            for model in config["chains"]["chain"]["MODEL_CLASS_MAP"].keys():
                config["chains"]["chain"]["MODEL_CLASS_MAP"][model]["class"] = MODEL_MAPPING[model]

            EMBEDDING_MAPPING = {
                "OpenAIEmbeddings": OpenAIEmbeddings,
                "HuggingFaceEmbeddings": HuggingFaceEmbeddings
            }
            for model in config["utils"]["embeddings"]["EMBEDDING_CLASS_MAP"].keys():
                config["utils"]["embeddings"]["EMBEDDING_CLASS_MAP"][model]["class"] = EMBEDDING_MAPPING[model]

            return config

        except Exception as e: 
            raise e
