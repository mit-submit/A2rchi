from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field


@dataclass
class ServiceDefinition:
    """Complete definition of a service"""
    name: str
    description: str
    category: str
    
    # Service configuration
    requires_image: bool = True
    requires_volume: bool = False
    auto_enable: bool = False
    
    # Dependencies
    depends_on: List[str] = field(default_factory=list)
    requires_services: List[str] = field(default_factory=list)
    
    # Secrets and config
    required_secrets: List[str] = field(default_factory=list)
    required_config_fields: List[str] = field(default_factory=list)
    
    # Port configuration
    default_host_port: Optional[int] = None
    default_container_port: Optional[int] = None
    port_config_path: Optional[str] = None
    
    # NEW: Volume naming strategy
    volume_name_pattern: Optional[str] = None  # e.g., "a2rchi-{name}", "a2rchi-pg-{name}"
    
    def get_volume_name(self, deployment_name: str) -> Optional[str]:
        """Generate volume name for this service"""
        if not self.requires_volume:
            return None
        
        if self.volume_name_pattern:
            return self.volume_name_pattern.format(name=deployment_name)
        else:
            # Default pattern
            return f"a2rchi-{deployment_name}"
    
    def get_image_name(self, deployment_name: str) -> Optional[str]:
        """Generate image name for this service"""
        if not self.requires_image:
            return None
        return f"{self.name}-{deployment_name}"
    
    def get_container_name(self, deployment_name: str) -> str:
        """Generate container name for this service"""
        return f"{self.name}-{deployment_name}"


class ServiceRegistry:
    """Central registry of all available services"""
    
    def __init__(self):
        self._services = {}
        self._register_default_services()
    
    def _register_default_services(self):
        """Register all default services"""
        
        # Infrastructure services (auto-enabled)
        self.register(ServiceDefinition(
            name='chromadb',
            description='Vector database for document storage and retrieval',
            category='infrastructure',
            requires_volume=True,
            auto_enable=True,
            default_host_port=8000,
            port_config_path='services.chromadb.chromadb_external_port'
        ))
        
        self.register(ServiceDefinition(
            name='postgres',
            description='PostgreSQL database for application data',
            category='infrastructure',
            requires_volume=True,
            auto_enable=True,
            volume_name_pattern="a2rchi-pg-{name}"
        ))
        
        # Application services
        self.register(ServiceDefinition(
            name='chatbot',
            description='Interactive chat interface for users to communicate with the AI agent',
            category='application',
            requires_volume=True,
            depends_on=['chromadb', 'postgres'],
            required_secrets=[],
            default_host_port=7861,
            default_container_port=7861,
            port_config_path='services.chat_app'
        ))
        
        self.register(ServiceDefinition(
            name='grafana',
            description='Monitoring dashboard for system and LLM performance metrics',
            category='application',
            requires_volume=True,
            depends_on=['postgres'],
            required_secrets=['GRAFANA_PG_PASSWORD'],
            default_host_port=3000,
            port_config_path='services.grafana.external_port',
            volume_name_pattern="a2rchi-grafana-{name}"
        ))
        
        self.register(ServiceDefinition(
            name='uploader',
            description='Admin interface for uploading and managing documents',
            category='application',
            depends_on=['chromadb'],
            required_secrets=['FLASK_UPLOADER_APP_SECRET_KEY', 'UPLOADER_SALT'],
            default_host_port=5003,
            default_container_port=5001,
            port_config_path='services.uploader_app'
        ))
        
        self.register(ServiceDefinition(
            name='grader',
            description='Automated grading service for assignments with web interface',
            category='application',
            requires_volume=True,
            depends_on=['postgres'],
            required_secrets=['ADMIN_PASSWORD'],
            default_host_port=7862,
            default_container_port=7861,
            port_config_path='services.grader_app'
        ))
        
        # Integration services
        self.register(ServiceDefinition(
            name='piazza',
            description='Integration service for Piazza posts and Slack notifications',
            category='integration',
            requires_volume=True,
            depends_on=['chromadb', 'postgres'],
            required_secrets=['PIAZZA_EMAIL', 'PIAZZA_PASSWORD', 'SLACK_WEBHOOK']
        ))
        
        self.register(ServiceDefinition(
            name='mattermost',
            description='Integration service for Mattermost channels',
            category='integration',
            required_secrets=['MATTERMOST_WEBHOOK', 'MATTERMOST_CHANNEL_ID_READ', 
                            'MATTERMOST_CHANNEL_ID_WRITE', 'MATTERMOST_PAK']
        ))
        
        self.register(ServiceDefinition(
            name='redmine-mailer',
            description='Email processing and Cleo/Redmine ticket management',
            category='integration',
            required_secrets=['IMAP_USER', 'IMAP_PW', 'REDMINE_URL', 'REDMINE_USER', 
                            'REDMINE_PW', 'REDMINE_PROJECT', 'SENDER_SERVER', 'SENDER_PORT', 
                            'SENDER_REPLYTO', 'SENDER_USER', 'SENDER_PW']
        ))

        self.register(ServiceDefinition(
            name='benchmarking',
            depends_on=['chromadb', 'postgres'],
            requires_volume=True, 
            description='Benchmarking runtime, its not really a service but under the hood it will be',
            category='benchmarking runtime', # not technically a service
        ))
    
    def register(self, service_def: ServiceDefinition):
        """Register a new service definition"""
        self._services[service_def.name] = service_def
    
    def get_service(self, name: str) -> ServiceDefinition:
        """Get service definition by name"""
        if name not in self._services.keys():
            raise ValueError(f"Unknown service: {name}")
        return self._services[name]
    
    def get_all_services(self) -> Dict[str, ServiceDefinition]:
        """Get all registered services"""
        return self._services.copy()
    
    def get_services_by_category(self, category: str) -> Dict[str, ServiceDefinition]:
        """Get services by category"""
        return {name: svc for name, svc in self._services.items() 
                if svc.category == category}
    
    def get_application_services(self) -> Dict[str, ServiceDefinition]:
        """Get user-selectable application services"""
        return self.get_services_by_category('application')
    
    def get_integration_services(self) -> Dict[str, ServiceDefinition]:
        """Get integration services"""
        return self.get_services_by_category('integration')
    
    def get_infrastructure_services(self) -> List[str]:
        """Get services that should be auto-enabled"""
        return [name for name, svc in self._services.items() if svc.auto_enable]
    
    def resolve_dependencies(self, enabled_services: List[str]) -> List[str]:
        """Resolve all dependencies for enabled services"""
        resolved = set()
        to_process = list(enabled_services)
        
        while to_process:
            service_name = to_process.pop(0)
            if service_name in resolved:
                continue
                
            if service_name not in self._services:
                continue  # Skip unknown services (like data sources)
                
            service_def = self._services[service_name]
            resolved.add(service_name)
            
            # Add dependencies
            for dep in service_def.depends_on:
                if dep not in resolved:
                    to_process.append(dep)
            
            # Add required services
            for req in service_def.requires_services:
                if req not in resolved:
                    to_process.append(req)
        
        # Add auto-enable services
        for name, svc in self._services.items():
            if svc.auto_enable:
                resolved.add(name)
        
        return list(resolved)
    
    def get_required_secrets(self, enabled_services: List[str]) -> Set[str]:
        """Get all required secrets for enabled services"""
        secrets = set()
        for service_name in enabled_services:
            if service_name in self._services:
                service_def = self._services[service_name]
                secrets.update(service_def.required_secrets)
        return secrets


# Global registry instance
service_registry = ServiceRegistry()
