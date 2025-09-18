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
from a2rchi.utils.logging import get_logger


logger = get_logger(__name__)

class MultiConfigManager:

    def __init__(self, config_paths_list: List[Path], enabled_services, gpu: bool, env):
        assert(len(config_paths_list) > 0)
        self.raw_configs = [(file, self._load_raw_config(file)) for file in config_paths_list]
        self.enabled_services = enabled_services
        self.gpu = gpu # right now this doesnt do anything, but perhaps later it might be useful to confirm whether or
                       # not the user needs gpus based on their configs and throw an error if the gpu flag is not set
        self.using_sso = False
        self.embedding_models_used = ""
        self.env = env

        # validates configs and defines some helpful vars for interacting with this class
        # later on in template manager
        self._validate_configs(self.raw_configs, enabled_services)

    # in the future, we might want to validate that each pipeline has at least one required model, 
    # i leave this as an exercise to the reader 
    def _validate_configs(self, raw_configs, enabled_services): 
        
        static_requirements = self._build_static_requirements(enabled_services) 
        logger.debug(f"Found the following static_config values: {static_requirements}")
        static_config_values = [self._get_values(config, static_requirements) for _, config in raw_configs]
        
        # get all the prompts will raise an error if a file contains no pipelines
        all_prompts = [self._get_all_prompts(conf) for conf in raw_configs]
        self.unique_prompt_paths = list(set.union(*all_prompts))
        logger.debug(f'the following unique prompt file paths  were found: {self.unique_prompt_paths}')

        all_models = [self._get_all_models(conf) for conf in raw_configs]
        self.unique_models_used = list(set.union(*all_models))
        logger.debug(f'the following unique models were found: {self.unique_models_used}')

        # if the set made out of all these values is not 1, we have a mistmatch handle by raising an error
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

        # use the first raw config as the base for grabbing of static info
        self.base_file = self.raw_configs[0][0]
        self.base_config = self.raw_configs[0][1]

        if 'benchmarking' not in enabled_services: 
            enabled_set = set(enabled_services)
        else: 
            enabled_set = {'postgres', 'chromadb'}
        self.service_configs = dict(filter(lambda kv: kv[0] in enabled_set, self.base_config.items()))

        # handle the combination of input lists across all files to get copied in
        input_lists = [conf.get('data_manager', {}).get('input_lists', []) for _, conf in raw_configs]
        input_list = list(set(reduce(lambda a,b: a + b, input_lists)))
        self.input_list = input_list

        # get using sso 
        sso_configuration = [conf.get('utils', {}).get('sso', {}).get("enabled", False) for _, conf in raw_configs]
        using_sso = any(sso_configuration)
        self.using_sso = using_sso

        # get embeddings being used
        embedding_models_used = list(set([conf.get("data_manager", {}).get("embedding_name", "") 
                                          for _, conf in raw_configs]))

        self.embedding_models_used = " ".join(embedding_models_used)

    def _get_all_prompts(self, config): 
        file, conf = config
        pipelines = conf.get('a2rchi', {}).get('pipelines', [])

        if not pipelines: 
            raise EnvironmentError(f'No pipelines found in: {file}')

        file_prompts = [conf.get('a2rchi', {}).get('pipeline_map', {}).get(pipeline, {}).get('prompts', {}) 
                        for pipeline in pipelines]

        unique_file_paths = reduce(lambda acc,b:
                acc | set(b.get('required', {}).values()) | set(b.get('optional', {}).values()),
               file_prompts, 
               set())

        return unique_file_paths
        
    def _get_all_models(self, config): 
        _, conf = config
        pipelines = conf.get('a2rchi', {}).get('pipelines', [])

        file_models = [conf.get('a2rchi', {}).get(pipeline, {}).get('models', {}) for pipeline in pipelines]

        unique_models_used = reduce(lambda acc,b:
                acc | set(b.get('required', {}).values()) | set(b.get('optional', {}).values()),
               file_models, 
               set())

        return unique_models_used

    def _build_static_requirements(self, enabled_services: List[str]):
        PATH_TO_BASE_CONFIG = "base-config.yaml"

        # render full config to get the current requirements for each service
        # this code below just gets the globals and the service keys as is currently
        # listed by the base config
        empty_config = {'name': 'base'}
        config_template = self.env.get_template(PATH_TO_BASE_CONFIG)
        template  = config_template.render(**empty_config)
        default_full_config  = yaml.safe_load(template)

        # collect  requirements to check as a list of strings separated by dots
        global_configs = default_full_config.get('global', {})
        global_configs_list = self.get_all_keys(global_configs, path=('global',))
        global_static_requirements = [".".join(l) for l in global_configs_list]

        enabled_set = set(enabled_services)
        if "benchmarking" not in enabled_set: 
            service_configs = default_full_config.get('services', {})
            enabled_service_configs = dict(filter(lambda kv: kv[0] in enabled_set, service_configs.items()))
            enabled_services_list = self.get_all_keys(enabled_service_configs, path=('services',))
            service_static_requirements = [".".join(l) for l in enabled_services_list] 
        else:
            service_static_requirements = []

        return global_static_requirements + service_static_requirements
            
    def get_all_keys(self, d: Dict[str, Dict | str] | Any, path: Tuple= ()) -> List[Tuple[str]]:
        def get_key_paths(d: dict | Any, path=()):
            # walk all the nested directories recursively and add each key path 
            # to a tuple that gets accumulated into a set 
            # (also dont listen to the lsp this reduce is right, lsp doesnt know anything)
            return reduce(
                    lambda accumulator, kv: accumulator | (
                        get_key_paths(kv[1], path + (kv[0],))
                        if isinstance(kv[1],dict)
                        else {path + (kv[0],)}),
                      d.items(),
                      set() # type: Set[Tuple[str]]
                  )

        set_of_keys = get_key_paths(d, path=path)
        return list(set_of_keys)
    
    def _get_values(self, config, static_requirements):
        """ get a list of the static requirements """
        return tuple([(req, self._get_value(req, config)) for req in static_requirements])

    def _get_value(self, req, config):
        keys = req.split(".")

        def reducer(obj, field):
            return obj.get(field) if isinstance(obj, dict) else None

        res = reduce(reducer, keys, config)
        return res

    def _load_raw_config(self, file_path):
        """literally just loads a config file from the file into a bunch of dictionaries"""
        with open(file_path, "r") as f: 
            config = yaml.safe_load(f)
        return config
        
    def get_sso(self):
        return self.using_sso

    def get_embedding_name(self):
        return self.embedding_models_used

    def get_input_lists(self):
        return self.input_list

    def get_raw_configs(self):
        return self.raw_configs

    def get_service_configs(self):
        return self.service_configs

    def get_grader_rubrics(self):
        if 'grader' not in self.enabled_services:
            raise EnvironmentError(f"Grader is not an enabled service")
        num_problems = self.base_config.get('services', {}).get('grader_app', {}).get('num_problems', 1)
        return [f"solution_with_rubric_{i}" for i in range(1, num_problems + 1)]

    def get_unique_prompt_file_paths(self):
        return self.unique_prompt_paths

    def get_models_configs(self):
        return self.unique_models_used
