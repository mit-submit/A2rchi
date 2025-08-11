from langchain_openai import OpenAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings

import os
import yaml

# DEFINITIONS
CONFIG_PATH = "/root/A2rchi/config.yaml"

def load_config(map: bool = False):
    """
    Load the config.yaml file.
    Optionally maps models to the corresponding class.
    """

    with open(CONFIG_PATH, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    # change the model class parameter from a string to an actual class
    if map:

        from a2rchi.chains.models import OpenAILLM, DumbLLM, LlamaLLM, AnthropicLLM, HuggingFaceOpenLLM, HuggingFaceImageLLM, VLLM
        from a2rchi.utils.sso_scraper import CERNSSOScraper
        
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