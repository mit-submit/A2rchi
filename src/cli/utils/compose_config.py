from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class ServiceConfig:
    """Simple service configuration class"""
    
    def __init__(self, **kwargs):
        # Set defaults
        self.enabled = False
        self.image_name = ""
        self.tag = ""
        self.container_name = ""
        self.volume_name = ""
        self.required_secrets = []
        self.required_config_fields = []
        self.port_host = None
        self.port_container = None
        
        # Override with provided values
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise ValueError(f"Unknown service config parameter: {key}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for template rendering"""
        return {
            'enabled': self.enabled,
            'image_name': self.image_name,
            'tag': self.tag,
            'container_name': self.container_name,
            'volume_name': self.volume_name,
            'required_secrets': self.required_secrets,
            'required_config_fields': self.required_config_fields,
            'port_host': self.port_host,
            'port_container': self.port_container,
        }


class ComposeConfig:
    """Docker Compose configuration for template rendering"""
    
    def __init__(self, name: str, base_dir: Path, tag: str, use_podman: bool, 
                 gpu_ids: Any, host_mode: bool, verbosity: int, bench_out: str):
        self.name = name
        self.base_dir = base_dir
        self.tag = tag
        self.use_podman = use_podman
        self.gpu_ids = gpu_ids
        self.host_mode = host_mode
        self.verbosity = verbosity
        self.benchmarking_dest = bench_out
        
        # Initialize all services as disabled
        self.services = {
            'chromadb': ServiceConfig(),
            'postgres': ServiceConfig(),
            'chatbot': ServiceConfig(),
            'grafana': ServiceConfig(),
            'uploader': ServiceConfig(),
            'grader': ServiceConfig(),
            'piazza': ServiceConfig(),
            'mattermost': ServiceConfig(),
            'redmine-mailer': ServiceConfig(),
            'benchmarking': ServiceConfig(),
        }
        
        # Data sources
        self.use_redmine = False
        self.use_jira = False
        
        # Store required secrets (set by ServiceBuilder)
        self._required_secrets = set()
    
    def get_service(self, name: str) -> ServiceConfig:
        """Get service configuration by name"""
        if name not in self.services:
            raise ValueError(f"Unknown service: {name}")
        return self.services[name]
    
    def enable_service(self, name: str, **config) -> None:
        """Enable a service with the given configuration"""
        if name not in self.services:
            raise ValueError(f"Unknown service: {name}")
        
        config['enabled'] = True
        self.services[name] = ServiceConfig(**config)
    
    def get_enabled_services(self) -> List[str]:
        """Get list of enabled service names"""
        return [name for name, service in self.services.items() if service.enabled]
    
    def get_required_volumes(self) -> List[str]:
        """Get list of required volume names"""
        volumes = []
        for service in self.services.values():
            if service.enabled and service.volume_name:
                volumes.append(service.volume_name)
        
        # Add GPU models volume if needed
        if self.gpu_ids:
            volumes.append('a2rchi-models')
        
        return list(set(volumes))
    
    def get_required_secrets(self) -> List[str]:
        """Get all required secrets from enabled services"""
        secrets = []
        for service in self.services.values():
            if service.enabled:
                secrets.extend(service.required_secrets)
        return list(set(secrets))
    
    def to_template_vars(self) -> Dict[str, Any]:
        """Convert to dictionary suitable for Jinja2 template rendering"""
        vars_dict = {
            'name': self.name,
            'tag': self.tag,
            'use_podman': self.use_podman,
            'gpu_ids': self.gpu_ids,
            'host_mode': self.host_mode,
            'verbosity': self.verbosity,
            'use_redmine': self.use_redmine,
            'use_jira': self.use_jira,
            'required_volumes': self.get_required_volumes(),
            'required_secrets': list(self._required_secrets),  # Use simplified secrets from CLI
            'benchmarking_dest': self.benchmarking_dest,
        }
        
        # Add service configurations
        for name, service in self.services.items():
            vars_dict[name] = service.to_dict()
            
            # Add individual service template vars for backward compatibility
            if service.enabled:
                vars_dict[f'{name.replace("-", "_")}_enabled'] = True
                vars_dict[f'{name.replace("-", "_")}_image'] = service.image_name
                vars_dict[f'{name.replace("-", "_")}_tag'] = service.tag
                vars_dict[f'{name.replace("-", "_")}_container_name'] = service.container_name
                vars_dict[f'{name.replace("-", "_")}_volume_name'] = service.volume_name
        
        return vars_dict
