import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Set

import click

from src.cli.service_registry import service_registry
from src.cli.source_registry import source_registry
from src.cli.utils.service_builder import ServiceBuilder
from src.utils.logging import get_logger

logger = get_logger(__name__)

def check_docker_available() -> bool:
    """Check if Docker is available and not just Podman emulation."""
    if not shutil.which("docker"):
        return False
    
    try:
        # Run 'docker --version' to check if it's actually Docker
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        # If stderr contains podman message, it's actually podman emulation
        if result.returncode == 0 and "podman" not in result.stderr.lower():
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    
    return False

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
    
    available_sources = [
        name for name in source_registry.names() if name != 'links'
    ]
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
        from src.cli.utils.service_builder import ServiceBuilder
        available_services = ServiceBuilder.get_available_services()
        service_list = '\n'.join([f"  {name}: {desc}" for name, desc in available_services.items()])
        raise click.ClickException(
            f"No services selected. Please specify at least one service using --services.\n"
            f"Available services:\n{service_list}\n"
            f"Example: --services chatbot,grafana"
        )


def log_dependency_resolution(services: List[str], enabled_services: List[str]) -> None:
    """Log which dependencies were auto-enabled"""
    resolved_services = service_registry.resolve_dependencies(enabled_services)
    service_only_resolved = [s for s in resolved_services if s in service_registry.get_all_services()]
    
    if set(service_only_resolved) != set(services):
        added_services = set(service_only_resolved) - set(services)
        if added_services:
            logger.info(f"Auto-enabling dependencies: {', '.join(added_services)}")


def handle_existing_deployment(base_dir: Path, name: str, force: bool, dry: bool, 
                              use_podman: bool) -> None:
    """Handle existing deployment - either remove it or raise error"""
    if base_dir.exists():
        if force:
            if not dry:
                logger.info(f"Removing existing deployment at {base_dir}")
                from src.cli.managers.deployment_manager import \
                    DeploymentManager
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


def show_service_urls(services: List[str], a2rchi_config: Dict[str, Any], host_mode: bool) -> None:
    """Show service URLs using registry configuration"""
    for service_name in services:
        if service_name not in service_registry.get_all_services():
            continue
            
        service_def = service_registry.get_service(service_name)
        if not service_def.port_config_path:
            continue
            
        try:
            # Navigate config path to get port
            config_value = a2rchi_config
            for key in service_def.port_config_path.split('.'):
                config_value = config_value[key]
                
            if isinstance(config_value, dict):
                if host_mode:
                    port = config_value.get('port', service_def.default_container_port)
                else:
                    port = config_value.get('external_port', service_def.default_host_port)
            else:
                port = config_value
                
            if port:
                logger.info(f"{service_name.title()}: http://localhost:{port}")
                
        except (KeyError, TypeError):
            # Use default port if config navigation fails
            fallback_port = (
                service_def.default_container_port if host_mode else service_def.default_host_port
            )
            if fallback_port:
                logger.info(f"{service_name.title()}: http://localhost:{fallback_port}")


def log_deployment_start(name: str, services: List[str], sources: List[str], dry: bool) -> None:
    """Log deployment start information"""
    logger.info(f"{'[DRY RUN] ' if dry else ''}Creating deployment '{name}' with services: {', '.join(services)}")
    if sources:
        logger.info(f"Data sources: {', '.join(sources)}")


def log_deployment_success(name: str, service_only_resolved: List[str], services: List[str], 
                          config_manager, host_mode) -> None:
    """Log successful deployment and show service URLs"""
    print(f"A2RCHI deployment '{name}' created successfully!")
    print(f"Services running: {', '.join(service_only_resolved)}")
    #All services are part of static configuration and equal for all configs
    a2rchi_config = config_manager.get_configs()[0]
    show_service_urls(services, a2rchi_config, host_mode=host_mode)