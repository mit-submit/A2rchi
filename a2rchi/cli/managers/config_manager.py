from a2rchi.utils.logging import get_logger
from a2rchi.cli.managers.templates_manager import BASE_CONFIG_TEMPLATE
from pathlib import Path
from typing import Dict, List, Any, Tuple
from functools import reduce
import os
import yaml

logger = get_logger(__name__)

static_fields = ['global','services']

class ConfigurationManager:
    """Manages A2rchi configuration loading and validation"""
    
    def __init__(self, config_paths_list: List[str], env):
        self.configs = []
        for config_filepath in config_paths_list:
            config_filepath = Path(config_filepath)
            try:
                config = self._load_config(config_filepath)
                self._append(config)
            except Exception as e:
                logger.error(f'Config {config_filepath} could not be loaded due to {str(e)}')

        assert(len(self.configs)>0)        
        self.config = self.configs[0]

        self.env = env
    
    def _load_config(self, config_filepath) -> Dict[str, Any]:
        """Load and validate basic structure of config file"""
        if not config_filepath.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_filepath}")

        with open(config_filepath, 'r') as f:
            config = yaml.safe_load(f)

        if not config:
            raise ValueError("Configuration file is empty or invalid")

        return config
    
    def _append(self,config):
        """Appends configuration to the config list if the static portions are equivalent to the previous one."""

        if len(self.configs)==0:
            self.configs = [config]
        else:
            previous_config = self.configs[-1]

            #This does not assume the static_fields to be required 
            for static_field in static_fields:
                if static_field in previous_config.keys():
                    if not static_field in config.keys():
                        raise ValueError(f"The field {static_field} must be present in all configurations.")
                    
                    if previous_config[static_field] != config[static_field]:
                        raise ValueError(f"The field {static_field} must be consistent across all configurations.")

            self.configs.append(config)
    
    def _get_static_required_fields_for_services(self, services: List[str]) -> List[str]:
        """Get required configuration fields based on enabled services"""
        if not services:
            return []  # No validation needed if no services selected
            
        # Base fields always required
        requirements = [
            'name', 
            'a2rchi.pipelines'
        ]

        # Services that have additional required fields
        service_fields = self._get_service_fields()

        # Add service-specific fields
        for service in services:
            if service in service_fields:
                requirements.extend(service_fields[service])
                
        return requirements
    
    def _get_service_fields(self):
        """Generates dictionary of service fields for services that have additional required fields"""
        template = self.env.get_template(BASE_CONFIG_TEMPLATE)
        default_config = template.render()
        default_config = yaml.safe_load(default_config)

        services = default_config['services']
        service_fields = {}

        for service_name, service_configs in services.items():
            blank_configs = [key for key,value in service_configs.items() if (value is None) or (value=='')]
            service_fields[service_name] = [f'services.{service_name}.{key}' for key in blank_configs]

        return service_fields
    
    def _get_active_pipeline_requirements(self,config) -> List[str]:
        """Get required prompt and/or model fields for the active pipeline"""
        pipeline_names = config.get('a2rchi', {}).get('pipelines', "")
        pipeline_requirements = []
        for pipeline_name in pipeline_names:
            required_prompts = config.get('a2rchi', {}).get('pipeline_map', {}).get(pipeline_name, {}).get('prompts', {}).get('required', {})
            required_models = config.get('a2rchi', {}).get('pipeline_map', {}).get(pipeline_name, {}).get('models', {}).get('required', {})
        
            pipeline_requirements.extend([f'a2rchi.pipeline_map.{pipeline_name}.prompts.required.{prompt_name}' for prompt_name in required_prompts.keys()])
            pipeline_requirements.extend([f'a2rchi.pipeline_map.{pipeline_name}.models.required.{model_name}' for model_name in required_models.keys()])
        
        return pipeline_requirements
    
    def _validate_config(self, required_fields: List[str], config) -> None:
        """Validate that all required fields are present in config"""
        for field in required_fields:
            keys = field.split('.')
            value = config
            for key in keys:
                if key not in value:
                    raise ValueError(f"Missing required field: '{field}' in the configuration")
                value = value[key]

    def validate_configs(self, services: List[str]):
        """Validate that all required fields are present in each config"""
        
        static_requirements = self._get_static_required_fields_for_services(services)

        for config in self.configs:
            pipeline_requirements = self._get_active_pipeline_requirements(config)
            required_fields = static_requirements+pipeline_requirements
            self._validate_config(required_fields,config)

        # get embeddings being used
        embedding_models_used = list(set([conf.get("data_manager", {}).get("embedding_name", "") 
                                          for conf in self.configs]))

        self.embedding_models_used = " ".join(embedding_models_used)

        # handle the combination of input lists across all files to get copied in
        input_lists = [conf.get('data_manager', {}).get('input_lists', []) for conf in self.configs]
        input_list = list(set(reduce(lambda a,b: a + b, input_lists)))
        self.input_list = input_list

    
    def get_configs(self) -> Dict[str, Any]:
        """Get the loaded configuration"""
        return self.configs
    
    def get_pipeline_configs(self) -> Dict[str, Any]:
        """Get the active pipeline configuration"""
        pipeline_configs = []
        for config in self.configs:
            pipeline_names = config.get("a2rchi", {}).get("pipelines")
            for pipeline_name in pipeline_names:
                pipeline_map = config.get("a2rchi", {}).get("pipeline_map", {})
                pipeline_configs.append(pipeline_map.get(pipeline_name, {}))

        if len(pipeline_configs)==0:
            return [{}]

        return pipeline_configs
    
    def get_models_configs(self) -> Dict[str, Any]:
        """Get models configuration from active pipeline"""
        pipeline_configs = self.get_pipeline_configs()
        model_configs = []
        for pipeline_config in pipeline_configs:
            model_configs.append(pipeline_config.get("models", {}))

        return model_configs
    
    def get_prompts_config(self) -> Dict[str, Any]:
        """Get prompts configuration from active pipeline"""
        pipeline_configs = self.get_pipeline_configs()
        prompt_configs = []
        for pipeline_config in pipeline_configs:
            prompt_configs.append(pipeline_config.get("prompts", {}))

        return prompt_configs
    
    def get_interface_config(self, interface_name: str) -> Dict[str, Any]:
        """Get configuration for a specific interface"""
        return self.config.get("services", {}).get(interface_name, {})
    
    def get_service_config(self, service_name: str) -> Dict[str, Any]:
        """Get configuration for a specific service"""
        return self.config.get("utils", {}).get(service_name, {})
    
    def get_embedding_name(self):
        return self.embedding_models_used
    
    def get_input_lists(self):
        return self.input_list
    
    def get_sso(self):
        # get using sso 
        sso_configuration = [conf.get('utils', {}).get('sso', {}).get("enabled", False) for conf in self.configs]
        using_sso = any(sso_configuration)
        return using_sso
    
    def _get_all_models(self, config): 
        pipelines = config.get('a2rchi', {}).get('pipelines', [])

        file_models = [config.get('a2rchi', {}).get(pipeline, {}).get('models', {}) for pipeline in pipelines]

        unique_models_used = reduce(lambda acc,b:
                acc | set(b.get('required', {}).values()) | set(b.get('optional', {}).values()),
               file_models, 
               set())

        return unique_models_used
