"""
Module to generate a benchmarking config to run through multiple conifigs could possbly be undone by the changes made in the
multi config support pr

P.s. ---------------------------------------------------------------------------------------------------
For anyone who looks at this, I decided to make everything internal to the class functionally programmed 
(if you hate it too bad, this is based)
"""
from pathlib import Path
from typing import Dict, Any, List, Tuple
from functools import reduce

import yaml
import uuid

class BenchmarkingConfigManager:

    def __init__(self, config_dir: str, gpu: bool):
        self.config_path = Path(config_dir)
        self.aggregate_config = self._create_aggregate_config()
        self.gpu = gpu

    def _create_aggregate_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration directory not found: {self.config_path}")

        files = list(self.config_path.iterdir())
        valid, failure_file, prompts, input_list = self._validate_and_aggregate_configs(files)


        if valid: 
           # get the datamanager config from the first file 
           with open(files[0], "r") as f:
               full_config = yaml.safe_load(f)

               # this is just to bypass the secrets manager and everything else, 
               # think of it like a dummy config which allows for everything to get loaded 
               # into the container, but that isnt actually doing anything in terms of runtime
               build_template = {
                       'data_manager':{
                           'input_lists': input_list,
                           'chromadb_port': full_config['data_manager'].get('chromadb_port'),
                           'chromadb_external_port': full_config['data_manager'].get('chromadb_external_port')
                           },
                 
                       'a2rchi': {
                           'pipelines': ['QAPipeline'],
                           'pipeline_map': {
                               'QAPipeline': {'prompts': prompts,
                                              'models':{
                                                  "required": {
                                                      "model1_name": "OpenAI",
                                                      "model2_name": "Anthropic",
                                                      },
                                                  }, 
                                                },
                               },
                           }
                       }


           # so the secret manager loads all of the secrets
           self.models_config = [build_template.get("a2rchi", {}).get("pipeline_map", {}).get("QAPipeline").get("models")]
           return build_template
        else:
            raise EnvironmentError(f"Configurations provided do not match required args: {failure_file}")

    def _validate_and_aggregate_configs(self, files: List[Path]):
        static_info_and_input_lists = [self._extract_static_info_and_input_lists(file) for file in files]
        static_info, input_lists = zip(*static_info_and_input_lists)

        prompts = [self._extract_prompt(file) for file in files]
        to_iterate = list(zip(files, static_info, prompts, input_lists))

        return reduce(self._combine_and_validate_configs, to_iterate)

    def _extract_static_info_and_input_lists(self, file : Path) -> Tuple[Dict[str, Any], List[str]]:
        with open(file, "r") as f:
            full_config = yaml.safe_load(f)

        # Base fields always required
        static_requirements = [
            'data_manager.chromadb_port',
            'data_manager.chromadb_external_port',
        ]

        input_list = full_config.get('data_manager', {}).get('input_lists', [])

        res = dict([(req, self._get_value(req, full_config)) for req in static_requirements])
        return (res, input_list)
    
    def _get_value(self, req, config):

        keys = req.split(".")

        def reducer(obj, field):
            return obj.get(field) if isinstance(obj, dict) else None

        res = reduce(reducer, keys, config)

        if res is None: 
            raise KeyError(f"required values you gave resulted in a key error in the config")

        return res


    def _combine_and_validate_configs(self, file1, file2) -> Tuple[bool, Path, Dict[str, Any], List[str]]:
        # extract working config information
        filepath1, required_fields1, prompts1, input_list1 = file1
        filepath2, required_fields2, prompts2, input_list2 = file2

        #handle our boolean
        res_bool = all(required_fields1[key] == required_fields2[key] for key in required_fields1.keys())

        # return file (for in case we have an error)
        if res_bool: 
            res_file = filepath1
        else: 
            res_file = filepath2

        combined_prompts = self._combine_prompts(prompts1, prompts2)
        combined_input_list = list(set(input_list1 + input_list2))

        return (res_bool, res_file, combined_prompts, combined_input_list)

    def _extract_prompt(self, file: Path) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
        with open(file, "r") as f:
            config =  yaml.safe_load(f)

        a2rchi_conf = config.get('a2rchi', {})
        pipelines = a2rchi_conf.get('pipelines', [])
        pipeline_map = a2rchi_conf.get('pipeline_map', {})
        possible_prompts = [pipeline_map.get(pipe).get('prompts') for pipe in pipelines]

        prompts = reduce(self._combine_prompts, possible_prompts)

        return prompts
    

    def _generate_random_string(self): 
        return str(uuid.uuid4())[:8]

    def _combine_prompts(self, prompt1: Dict[str, Dict], prompt2:  Dict[str, Dict]) -> Dict[str, Dict]:
        # NOTE THAT THIS ASSUMES WE HAVE REQUIRED AND OPTIONAL PROMPTS

        # combining all the unique prompt paths (kind of ugly but it just list adds all the prompt paths and casts 
        # to set for uniqueness before going back to a list for iteration later)
        unique_filepaths = list(set(list(prompt1.get('optional', {}).values())  + list(prompt2.get('optional', {}).values()) \
                + list(prompt1['required'].values())  + list(prompt2['required'].values())))
        
        prompts_to_add = dict([(self._generate_random_string(), path) for path in unique_filepaths])

        res = {}
        res['required'] = prompts_to_add
        return res
        
    def get_config(self):
        return self.aggregate_config

    def get_models_configs(self):
        return self.models_config
