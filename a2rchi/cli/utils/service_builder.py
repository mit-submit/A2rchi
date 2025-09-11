from typing import List, Any, Set
from pathlib import Path

from a2rchi.cli.service_registry import service_registry
from a2rchi.cli.utils.compose_config import ComposeConfig


class ServiceBuilder:
    """Builds service configurations using the service registry"""
    
    @classmethod
    def get_available_services(cls) -> dict:
        """Get dictionary of available services for CLI help"""
        available_services = service_registry.get_application_services()
        integration_services = service_registry.get_integration_services()
        
        return {
            **{name: svc.description for name, svc in available_services.items()},
            **{name: svc.description for name, svc in integration_services.items()}
        }
    
    @classmethod
    def validate_services_selected(cls, **service_flags) -> List[str]:
        """Validate that at least one service is selected and return enabled services"""
        enabled_services = []
        available_services = service_registry.get_application_services()
        integration_services = service_registry.get_integration_services()
        
        # Check application services
        for service_name in available_services:
            if service_flags.get(service_name, False):
                enabled_services.append(service_name)
        
        # Check integration services  
        for service_name in integration_services:
            if service_flags.get(service_name, False):
                enabled_services.append(service_name)
        
        # Add data sources (these aren't in the registry as they're not services)
        # TODO: this needs to be changed,
        #       sources handled differently from services sure
        #       but not handled consistently and generalized throughout CLI
        if service_flags.get('redmine', False):
            enabled_services.append('redmine')
        if service_flags.get('jira', False):
            enabled_services.append('jira')
        
        if not enabled_services:
            available_list = '\n'.join([
                f"  --{name}: {svc.description}" 
                for name, svc in {**available_services, **integration_services}.items()
            ])
            raise ValueError(
                f"No services selected. Please select at least one service:\n\n"
                f"Available services:\n{available_list}\n\n"
                f"Example: a2rchi create --name mybot --config config.yaml --env-file .env --chatbot --grafana"
            )
        
        return enabled_services
    
    @staticmethod  
    def build_compose_config(name: str, verbosity: int, base_dir: Path, 
                        enabled_services: List[str], 
                        secrets: Set[str] = None,
                        **other_flags) -> ComposeConfig:
        """Build complete compose configuration using ONLY service registry"""
        
        # Extract parameters from other_flags
        tag = other_flags.get('tag', '2000') 
        podman = other_flags.get('podman', False)
        gpu_ids = other_flags.get('gpu_ids', None)
        host_mode = other_flags.get('hostmode', other_flags.get('host_mode', False))
        
        # Resolve all dependencies using registry
        all_services = service_registry.resolve_dependencies(enabled_services)
        
        config = ComposeConfig(
            name=name, base_dir=base_dir, tag=tag, use_podman=podman,
            gpu_ids=gpu_ids, host_mode=host_mode, verbosity=verbosity
        )
        
        # Store required secrets
        if secrets:
            config._required_secrets = secrets
        
        # Enable all resolved services using ONLY registry data
        for service_name in all_services:
            if service_name not in service_registry.get_all_services():
                continue  # Skip data sources
                
            service_def = service_registry.get_service(service_name)
            
            # Build configuration purely from registry
            service_config = {
                'required_secrets': service_def.required_secrets,
                'required_config_fields': service_def.required_config_fields,
            }
            
            # Add image configuration if needed
            if service_def.requires_image:
                service_config.update({
                    'image_name': service_def.get_image_name(name),
                    'tag': tag,
                    'container_name': service_def.get_container_name(name),
                })
            
            # Add volume configuration if needed
            volume_name = service_def.get_volume_name(name)
            if volume_name:
                service_config['volume_name'] = volume_name
            
            # Add port configuration
            if service_def.default_host_port:
                service_config['port_host'] = service_def.default_host_port
            if service_def.default_container_port:
                service_config['port_container'] = service_def.default_container_port
            
            config.enable_service(service_name, **service_config)
        
        # Set data sources
        config.use_redmine = other_flags.get('redmine', 'redmine' in enabled_services)
        config.use_jira = other_flags.get('jira', 'jira' in enabled_services)
        
        return config