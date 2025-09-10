from pathlib import Path
from typing import Dict, List, Any

import yaml

class ConfigurationManager:
    """Manages A2rchi configuration loading and validation"""
    
    def __init__(self, config_filepath: str):
        self.config_filepath = Path(config_filepath)
        self.config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """Load and validate basic structure of config file"""
        if not self.config_filepath.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_filepath}")
            
        with open(self.config_filepath, 'r') as f:
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
    
    
    def validate_config(self, required_fields: List[str]) -> None:
        """Validate that all required fields are present in config"""
        for field in required_fields:
            keys = field.split('.')
            value = self.config
            for key in keys:
                if key not in value:
                    raise ValueError(f"Missing required field: '{field}' in the configuration")
                value = value[key]
    
    def get_config(self) -> Dict[str, Any]:
        """Get the loaded configuration"""
        return self.config
    
    def get_pipeline_configs(self) -> Dict[str, Any]:
        """Get the active pipeline configuration"""
        pipeline_names = self.config.get("a2rchi", {}).get("pipeline")
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
        return self.config.get("interfaces", {}).get(interface_name, {})
    
    def get_service_config(self, service_name: str) -> Dict[str, Any]:
        """Get configuration for a specific service"""
        return self.config.get("utils", {}).get(service_name, {})