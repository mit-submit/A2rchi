"""
Class to deal with the handling of multiple configurations in the pipeline

Some expected behaviours to clarify, if there are enabled services outside of benchmarking, 
we expect them to be present in all configs and have everythign working

if benchmarking is enabled, it doesnt matter we dont need to check the services

all stuff currenrtly under globals has to be checked and made sure to be consistent across all configs every time


P.S.
I was originally trying to write with all class internals being functional, (not important if you dont want it to be but i think its nice)
"""
from pathlib import Path
from typing import Dict, Any, List, Tuple
from functools import reduce

import yaml
import uuid


class MultiConfigManager:

    def __init__(self, config_paths_list: List[Path], enabled_services, gpu: bool):
        self.raw_configs = [(file, self._load_raw_config(file)) for file in config_paths_list]
        self.enabled_services = enabled_services
        self.gpu = gpu
        self.using_sso = False
        self.embedding_models_used = ""

        # validates configs and defines some helpful vars for interacting with this class
        self._validate_configs(self.raw_configs, enabled_services):

    def _validate_configs(self, raw_configs, enabled_services): 

        # WITH THE CHANGES TO THE BASE CONFIG ALL OF THIS LITERALLY JUST GOT CHANGED
        # handle actually checking that all the required static values are the same across all configs
        # this needs to be hotly debated as to what is actually kept and what is not 
        static_requirements = [
            'data_manager.chromadb_port',
            'data_manager.chromadb_external_port',
            'data_manager.chromadb_external_port',
        ]

        static_config_values = [self._get_values(config, static_requirements) for _, config in raw_configs]

        # if the set made out of all these values is not 1, we have a mistmatch 
        if len(set(static_config_values)) != 1: 
            mismatch = next(
                (
                    (file_index, req_index)
                    for file_index, req_list in enumerate(static_config_values)
                    for req_index, values in enumerate(req_list)
                    if len(set(values)) != 1
                ),
                None
            )
            assert(mismatch is not None) # should not be possible if were in this if statement
            failure_file = raw_configs[mismatch[0]][0]
            failure_requirement = static_requirements[mismatch[1]] 
            raise EnvironmentError(f"Necessarily Static values do not match across all files,\
                    Failing in file {failure_file} on requirement: {failure_requirement}")
        

        # handle the combination of input lists across all files to get  copied in
        input_lists = [conf.get('data_manager', {}).get('input_lists', []) for _, conf in raw_configs]
        input_list = list(set(reduce(lambda a,b: a + b, input_lists)))
        self.input_list = input_list

        # get using sso 
        sso_configuration = [conf.get('utils', {}).get('sso', {}).get("enabled", False) for _, conf in raw_configs]
        using_sso = any(sso_configuration)
        self.using_sso = using_sso

        # get embeddings being used
        embedding_models_used = list(set([conf.get("data_manager", {}).get("embedding_name", "") for _, conf in raw_configs]))
        self.embedding_models_used = " ".join(embedding_models_used)

        # TODO handle the ports configurations (for multi services)

        # TODO get the models config 

        return
    
    def _get_values(self, config, static_requirements):
        """ get a list of the static requirements """
        return [(req, self._get_value(req, config)) for req in static_requirements]

    def _get_value(self, req, config):
        keys = req.split(".")

        def reducer(obj, field):
            return obj.get(field) if isinstance(obj, dict) else None

        res = reduce(reducer, keys, config)
        if res is None: 
            raise KeyError(f"Required values given resulted in a key error in the config")
        return res
    
    def _load_raw_config(self, file_path):
        """literally just loads a config file from the file into a bunch of dictionaries"""
        with open(file_path, "r") as f: 
            config = yaml.safe_load(f)
        return config

    def _generate_random_string(self): 
        return str(uuid.uuid4())[:8]
        
    def get_sso(self):
        return self.using_sso

    # NOTE that this just returns the embedding models used in a string separated by spaces
    def get_embedding_name(self):
        return self.embedding_models_used

    def get_input_lists(self):
        return self.input_list

    #TODO: handle getting the ports config
    def get_ports_config(self):
        pass

    # TODO: implement 
    def get_pipelines_and_info(self):
        return

    # TODO: figure this out because the secrets manager needs this
    def get_models_configs(self):
        return self.models_config

