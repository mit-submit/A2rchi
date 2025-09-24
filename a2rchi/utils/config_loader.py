import os
import yaml

# DEFINITIONS
CONFIGS_PATH = "/root/A2rchi/configs/"

def load_config(map: bool = False, name: str = None):
    """
    Load the configuration specified by name, or the first one by default.
    Optionally maps models to the corresponding class.
    """

    if name is None:
        default_path = CONFIGS_PATH + os.listdir(CONFIGS_PATH)[0]
        with open(default_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    else:
        with open(CONFIGS_PATH+f"{name}.yaml", "r") as f:
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

def load_global_config(name: str = None):
    """
    Load the global part of the config.yaml file.
    This is assumed to be static.
    """

    if name is None:
        default_path = CONFIGS_PATH + os.listdir(CONFIGS_PATH)[0]
        with open(default_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    else:
        with open(CONFIGS_PATH+f"{name}.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

    return config["global"]

def load_utils_config(name: str = None):
    """
    Load the utils part of the config.yaml file.
    This is assumed to be static.
    """

    if name is None:
        default_path = CONFIGS_PATH + os.listdir(CONFIGS_PATH)[0]
        with open(default_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    else:
        with open(CONFIGS_PATH+f"{name}.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

    return config["utils"]

def load_data_manager_config(name: str = None):
    """
    Load the data_manager part of the config.yaml file.
    This is assumed to be static.
    """

    if name is None:
        default_path = CONFIGS_PATH + os.listdir(CONFIGS_PATH)[0]
        with open(default_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    else:
        with open(CONFIGS_PATH+f"{name}.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

    return config["data_manager"]

def load_services_config(name: str = None):
    """
    Load the services part of the config.yaml file.
    This is assumed to be static.
    """

    if name is None:
        default_path = CONFIGS_PATH + os.listdir(CONFIGS_PATH)[0]
        with open(default_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    else:
        with open(CONFIGS_PATH+f"{name}.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

    return config["services"]

def get_config_names():
    """
    Gets the available configurations names.
    """

    names = [n.replace('.yaml','') for n in os.listdir(CONFIGS_PATH)]
    return names



