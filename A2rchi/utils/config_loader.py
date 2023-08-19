import yaml

#import models so their classes could directly be added to the config file
from A2rchi.chains.models import OpenAILLM,DumbLLM,LlamaLLM

class Config_Loader():

    def __init__(self):
        self.config = self.load_config()

    def load_config(self):
        """
        Small function for loading the config.yaml file
        """

        try: 
            with open("config/config.yaml", "r") as f:
                config = yaml.load(f, Loader=yaml.FullLoader)

            #change the model class paramter from a string to an actual class
            MODEL_MAPPING = {
                "OpenAILLM":OpenAILLM,
                "DumbLLM": DumbLLM,
                "LlamaLLM": LlamaLLM
            }
            for model in config["chains"]["chain"]["MODEL_CLASS_MAP"].keys():
                config["chains"]["chain"]["MODEL_CLASS_MAP"][model]["class"] = MODEL_MAPPING[model]

            return config
        except Exception as e: 
            raise e
