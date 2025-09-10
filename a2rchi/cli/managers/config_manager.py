from pathlib import Path
from typing import Dict, List, Any
import os

import yaml

class ConfigurationManager:
    """Manages A2rchi configuration loading and validation"""
    
    def __init__(self, config_filepath: str):
        self.configs = {}
        if os.path.isdir(config_filepath):
            config_dir = Path(config_filepath)
            for yaml_file in config_dir.glob('*.yaml'):
                config_filepath = Path(yaml_file)
                try:
                    self.configs[config_filepath.stem] = self._load_config(config_filepath)
                except Exception as e:
                    print(f"Unable to load configuration file {yaml_file} : {e}")

            if len(self.configs)==0:
                raise ValueError(f"No suitable configurations were found in {config_filepath}")

        else:
            config_filepath = Path(config_filepath)
            self.configs[config_filepath.stem] = self._load_config(config_filepath)
        
        self.current_config = next(iter(self.configs.values()))
        
    def _load_config(self,config_filepath) -> Dict[str, Any]:
        """Load and validate basic structure of config file"""
        if not config_filepath.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_filepath}")
            
        with open(config_filepath, 'r') as f:
            config = yaml.safe_load(f)
            
        if not config:
            raise ValueError("Configuration file is empty or invalid")
            
        return config
    
    def get_required_fields_for_services(self, services: List[str]) -> List[str]:
        """Get required configuration fields based on enabled services"""
        if not services:
            return []  # No validation needed if no services selected
            
        # Base fields always required
        requirements = [
            'name', 
            'global.TRAINED_ON',
            'a2rchi.pipelines'
        ]

        pipeline_requirements = self._get_active_pipeline_requirements()
        requirements.extend(pipeline_requirements)
        
        # Services that have additional required fields
        service_fields = {
            'piazza': ['utils.piazza.network_id'],
            'grader': [
                'interfaces.grader_app.num_problems', 
                'interfaces.grader_app.local_rubric_dir', 
                'interfaces.grader_app.local_users_csv_dir',
            ]
        }

        # Add service-specific fields
        for service in services:
            if service in service_fields:
                requirements.extend(service_fields[service])
                
        return requirements
    
    def _get_active_pipeline_requirements(self) -> List[str]:
        """Get required prompt and/or model fields for the active pipeline"""
        pipeline_names = self.config.get('a2rchi', {}).get('pipelines', "")
        pipeline_requirements = []
        for pipeline_name in pipeline_names:
            required_prompts = self.config.get('a2rchi', {}).get('pipeline_map', {}).get(pipeline_name, {}).get('prompts', {}).get('required', {})
            required_models = self.config.get('a2rchi', {}).get('pipeline_map', {}).get(pipeline_name, {}).get('models', {}).get('required', {})
        
            pipeline_requirements.extend([f'a2rchi.pipeline_map.{pipeline_name}.prompts.required.{prompt_name}' for prompt_name in required_prompts.keys()])
            pipeline_requirements.extend([f'a2rchi.pipeline_map.{pipeline_name}.models.required.{model_name}' for model_name in required_models.keys()])
        
        return pipeline_requirements
    
    def validate_configs(self, required_fields: List[str]) -> None:
        for name, config in self.configs.items():
            try:
                self.validate_config(required_fields=required_fields,config=config)
            except ValueError as e:
                #TODO: Change prints to logs
                print(f'Removing config {name} due to: {e}')
                self.delete_config(name)

            
    def delete_config(self, name: str) -> None:
        removed_config = self.configs.pop(name)
        if len(self.configs)==0:
            raise ValueError(f"No available configurations.")
        
        if self.current_config == removed_config:
            self.current_config = self.configs.values[0]
    
    def validate_config(self, required_fields: List[str], config: Dict[str, Any]) -> None:
        """Validate that all required fields are present in config"""
        for field in required_fields:
            keys = field.split('.')
            value = config
            for key in keys:
                if key not in value:
                    raise ValueError(f"Missing required field: '{field}' in the configuration")
                value = value[key]
    
    def get_current_config(self) -> Dict[str, Any]:
        """Get the loaded configuration"""
        return self.current_config
    
    def get_configs(self) -> Dict[str, Any]:
        """Get all valid configurations"""
        return self.configs
    
    def get_pipeline_configs(self) -> Dict[str, Any]:
        """Get the active pipeline configuration"""
        pipeline_names = self.config.get("a2rchi", {}).get("pipelines")
        if not pipeline_names:
            return [{}]
        
        pipeline_configs = []
        for pipeline_name in pipeline_names:
            pipeline_map = self.config.get("a2rchi", {}).get("pipeline_map", {})
            pipeline_configs.append(pipeline_map.get(pipeline_name, {}))

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
        return self.current_configconfig.get("interfaces", {}).get(interface_name, {})
    
    def get_service_config(self, service_name: str) -> Dict[str, Any]:
        """Get configuration for a specific service"""
        return self.current_configconfig.get("utils", {}).get(service_name, {})