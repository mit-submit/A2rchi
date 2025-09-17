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

        from a2rchi.chains.models import OpenAILLM, DumbLLM, LlamaLLM, AnthropicLLM, HuggingFaceOpenLLM, HuggingFaceImageLLM, VLLM, OllamaInterface
        from a2rchi.utils.sso_scraper import CERNSSOScraper
        from langchain_openai import OpenAIEmbeddings
        from langchain_huggingface import HuggingFaceEmbeddings
        
        MODEL_MAPPING = {
            "AnthropicLLM": AnthropicLLM,
            "OpenAIGPT4": OpenAILLM,
            "OpenAIGPT35": OpenAILLM,
            "DumbLLM": DumbLLM,
            "LlamaLLM": LlamaLLM,
            "HuggingFaceOpenLLM": HuggingFaceOpenLLM,
            "HuggingFaceImageLLM": HuggingFaceImageLLM,
            "VLLM": VLLM,
            "OllamaInterface": OllamaInterface, 
        }
        for model in config["a2rchi"]["model_class_map"].keys():
            config["a2rchi"]["model_class_map"][model]["class"] = MODEL_MAPPING[model]

        EMBEDDING_MAPPING = {
            "OpenAIEmbeddings": OpenAIEmbeddings,
            "HuggingFaceEmbeddings": HuggingFaceEmbeddings
        }
        for model in config["data_manager"]["embedding_class_map"].keys():
            config["data_manager"]["embedding_class_map"][model]["class"] = EMBEDDING_MAPPING[model]

        # change the SSO class parameter from a string to an actual class
        if "sso" in config["utils"] and config["utils"]["sso"].get("enabled", False):
            SSO_MAPPING = {
                "CERNSSOScraper": CERNSSOScraper,
            }
            for sso_class in config["utils"]["sso"]["sso_class_map"].keys():
                config["utils"]["sso"]["sso_class_map"][sso_class]["class"] = SSO_MAPPING[sso_class]

    return config

def load_global_config():
    """
    Load the global part of the config.yaml file.
    This is assumed to be static.
    """

    with open(CONFIG_PATH, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    return config["global"]

def load_services_config():
    """
    Load the services part of the config.yaml file.

    This is assumed to be static during runtime
    """

    with open(CONFIG_PATH, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    return config["services"]

def load_utils_config():
    """
    Load the utils part of the config.yaml file.
    This is assumed to be static.
    """

    with open(CONFIG_PATH, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    return config["utils"]

def load_data_manager_config():
    """
    Load the data_manager part of the config.yaml file.
    This is assumed to be static.
    """

    with open(CONFIG_PATH, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    return config["data_manager"]
