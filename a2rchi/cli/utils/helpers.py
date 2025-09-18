from a2rchi.cli.service_registry import service_registry
from a2rchi.cli.utils.service_builder import ServiceBuilder
from a2rchi.utils.logging import get_logger

import click
from pathlib import Path
from typing import List, Set, Dict, Any

logger = get_logger(__name__)

def parse_gpu_ids_option(ctx, param, value):
    """Parse GPU IDs option - 'all' or comma-separated integers"""
    if value is None:
        return None
    if value.lower() == "all":
        return "all"
    try:
        return [int(x.strip()) for x in value.split(",")]
    except ValueError:
        raise click.BadParameter('--gpu-ids option must be "all" or comma-separated integers')

def parse_services_option(ctx, param, value):
    """Parse comma-separated services list using service registry"""
    if not value:
        return []
    
    # Get available services from registry
    available_services = list(ServiceBuilder.get_available_services().keys())
    services = [s.strip() for s in value.split(',')]
    
    invalid_services = [s for s in services if s not in available_services]
    if invalid_services:
        raise click.BadParameter(
            f'Invalid services: {", ".join(invalid_services)}. '
            f'Available: {", ".join(available_services)}'
        )
    
    return services

def parse_sources_option(ctx, param, value):
    """Parse comma-separated data sources list"""
    if not value:
        return []
    
    available_sources = ['jira', 'redmine']
    sources = [s.strip() for s in value.split(',')]
    
    invalid_sources = [s for s in sources if s not in available_sources]
    if invalid_sources:
        raise click.BadParameter(
            f'Invalid data sources: {", ".join(invalid_sources)}. '
            f'Available: {", ".join(available_sources)}'
        )
    
    return sources

def validate_services_selection(services: List[str]) -> None:
    """Validate that at least one service is selected, raise ClickException if not"""
    if not services:
        from a2rchi.cli.utils.service_builder import ServiceBuilder
        available_services = ServiceBuilder.get_available_services()
        service_list = '\n'.join([f"  {name}: {desc}" for name, desc in available_services.items()])
        raise click.ClickException(
            f"No services selected. Please specify at least one service using --services.\n"
            f"Available services:\n{service_list}\n"
            f"Example: --services chat_app,grafana"
        )


def log_dependency_resolution(services: List[str], enabled_services: List[str]) -> Set[str]:
    """Log which dependencies were auto-enabled"""
    resolved_services = service_registry.resolve_dependencies(enabled_services)
    service_only_resolved = [s for s in resolved_services if s in service_registry.get_all_services()]
    
    if set(service_only_resolved) != set(services):
        added_services = set(service_only_resolved) - set(services)
        if added_services:
            logger.info(f"Auto-enabling dependencies: {', '.join(added_services)}")
        return added_services
    return set("")


def handle_existing_deployment(base_dir: Path, name: str, force: bool, dry: bool, 
                              use_podman: bool) -> None:
    """Handle existing deployment - either remove it or raise error"""
    if base_dir.exists():
        if force:
            if not dry:
                logger.info(f"Removing existing deployment at {base_dir}")
                from a2rchi.cli.managers.deployment_manager import DeploymentManager
                deployment_manager = DeploymentManager(use_podman)
                try:
                    deployment_manager.delete_deployment(
                        deployment_name=name,
                        remove_images=False,
                        remove_volumes=False,
                        remove_files=True
                    )
                except Exception as e:
                    logger.info(f"Warning: Could not clean up existing deployment: {e}")
            else:
                logger.info(f"[DRY RUN] Would remove existing deployment at {base_dir}")
        else:
            raise click.ClickException(
                f"Deployment '{name}' already exists at {base_dir}.\n"
                f"Use --force to overwrite, or delete it first with: a2rchi delete --name {name}"
            )


def print_dry_run_summary(name: str, services: List[str], service_only_resolved: List[str], 
                         sources: List[str], required_secrets: Set[str], 
                         compose_config, other_flags: Dict[str, Any], base_dir: Path) -> None:
    """Print comprehensive dry run summary"""
    logger.info(f"[DRY RUN] Deployment summary:\n")
    click.echo(f"\tName: {name}")
    click.echo(f"\tRequested services: {', '.join(services)}")
    click.echo(f"\tAll services (with dependencies): {', '.join(service_only_resolved)}")
    
    if sources:
        click.echo(f"\tData sources: {', '.join(sources)}")
    
    click.echo(f"\tRequired secrets: {', '.join(sorted(required_secrets))}")
    click.echo(f"\tRequired volumes: {', '.join(compose_config.get_required_volumes())}")
    click.echo(f"\tContainer tool: {'Podman' if other_flags['podman'] else 'Docker'}")
    
    if other_flags.get('gpu_ids'):
        click.echo(f"\tGPU configuration: {other_flags['gpu_ids']}")
    
    click.echo(f"\tDeployment directory: {base_dir}\n")
    logger.info(f"[DRY RUN] Configuration and secrets are valid. Run without --dry to deploy.\n")


def show_service_urls(services: List[str], config_manager) -> None:
    """Show service URLs using registry configuration"""
    for service_name in services:
        if service_name not in service_registry.get_all_services():
            continue
            
        service_def = service_registry.get_service(service_name)
        if not service_def.port_config_path:
            continue
            
        try:
            # Navigate config path to get port
            config_value = config_manager.base_config
            for key in service_def.port_config_path.split('.'):
                config_value = config_value[key]
                
            if isinstance(config_value, dict):
                port = config_value.get('external_port', service_def.default_host_port)
            else:
                port = config_value
                
            if port:
                logger.info(f"{service_name.title()}: http://localhost:{port}")
                
        except (KeyError, TypeError):
            # Use default port if config navigation fails
            if service_def.default_host_port:
                logger.info(f"{service_name.title()}: http://localhost:{service_def.default_host_port}")


def log_deployment_start(name: str, services: List[str], sources: List[str], dry: bool) -> None:
    """Log deployment start information"""
    logger.info(f"{'[DRY RUN] ' if dry else ''}Creating deployment '{name}' with services: {', '.join(services)}")
    if sources:
        logger.info(f"Data sources: {', '.join(sources)}")


def log_deployment_success(name: str, service_only_resolved: List[str], services: List[str], 
                          config_manager) -> None:
    """Log successful deployment and show service URLs"""
    print(f"A2RCHI deployment '{name}' created successfully!")
    print(f"Services running: {', '.join(service_only_resolved)}")
    show_service_urls(services, config_manager)
