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
        from a2rchi.chains.models import OpenAILLM, DumbLLM, LlamaLLM, AnthropicLLM, HuggingFaceOpenLLM, HuggingFaceImageLLM, VLLM
        from a2rchi.utils.sso_scraper import CERNSSOScraper
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

            # change the SSO class parameter from a string to an actual class
            if "sso" in config["utils"] and config["utils"]["sso"].get("ENABLED", False):
                SSO_MAPPING = {
                    "CERNSSOScraper": CERNSSOScraper,
                }
                for sso_class in config["utils"]["sso"]["SSO_CLASS_MAP"].keys():
                    config["utils"]["sso"]["SSO_CLASS_MAP"][sso_class]["class"] = SSO_MAPPING[sso_class]

            return config

        except Exception as e: 
            raise e
        
        
def load_config_file():
    """
    Lightweight alternative import that doesn't do any class mapping, so doesn't require class imports
    """
    config_path = "/root/A2rchi/config.yaml"
    try:
        with open(config_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

        return config

    except Exception as e: 
        raise e
