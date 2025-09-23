from a2rchi.cli.managers.multi_config_manager import MultiConfigManager
from a2rchi.cli.managers.multi_template_manager import MultiTemplateManager
from a2rchi.cli.managers.config_manager import ConfigurationManager
from a2rchi.cli.managers.deployment_manager import DeploymentManager
from a2rchi.cli.managers.secrets_manager import SecretsManager
from a2rchi.cli.utils.helpers import *
from a2rchi.cli.utils.service_builder import ServiceBuilder
from a2rchi.cli.service_registry import service_registry
from a2rchi.cli.managers.templates_manager import TemplateManager
from a2rchi.cli.managers.volume_manager import VolumeManager
from a2rchi.utils.logging import setup_cli_logging, get_logger

from jinja2 import Environment, PackageLoader, select_autoescape, ChainableUndefined
from pathlib import Path
from typing import Dict, List, Any

import click
import os
import traceback

# DEFINITIONS
env = Environment(
    loader=PackageLoader("a2rchi.cli"),
    autoescape=select_autoescape(),
    undefined=ChainableUndefined,
)
A2RCHI_DIR = os.environ.get('A2RCHI_DIR',os.path.join(os.path.expanduser('~'), ".a2rchi"))

# this method  is really only used in the benchmarking


@click.group()
def cli():
    pass

@click.command()
@click.option('--name', '-n', type=str, required=True, help="Name of the a2rchi deployment")
@click.option('--config', '-c', 'config_files', type=str, help="Path to .yaml a2rchi configuration")
@click.option('--config-dir', '-cd', 'config_dir', type=str, help="Path to configs directory")
@click.option('--env-file', '-e', type=str, required=True, help="Path to .env file with secrets")
@click.option('--services', '-s', callback=parse_services_option, 
              help="Comma-separated list of services")
@click.option('--sources', '-src', callback=parse_sources_option,
              help="Comma-separated list of data sources: jira,redmine")
@click.option('--podman', '-p', is_flag=True, help="Use Podman instead of Docker")
@click.option('--gpu-ids', callback=parse_gpu_ids_option, help='GPU configuration: "all" or comma-separated IDs')
@click.option('--tag', '-t', type=str, default="2000", help="Image tag for built containers")
@click.option('--hostmode', 'host_mode', is_flag=True, help="Use host network mode")
@click.option('--verbosity', '-v', type=int, default=3, help="Logging verbosity level (0-4)")
@click.option('--force', '-f', is_flag=True, help="Force deployment creation, overwriting existing deployment")
@click.option('--dry', '--dry-run', is_flag=True, help="Validate configuration and show what would be created without actually deploying")
def create(name: str, config_files: str, config_dir: str,  env_file: str, services: list, sources: list, 
           force: bool, dry: bool, verbosity: int, **other_flags):
    """Create an A2RCHI deployment with selected services and data sources."""

    if not (bool(config_files) ^ bool(config_dir)): 
        raise click.ClickException(f"Must specify only one of config files or config dir")
    if config_dir: 
        config_path = Path(config_dir)
        configs = [Path(item) for item in config_path.iterdir() if item.is_file()]
    else: 
        configs = [Path(item) for item in config_files.split(",")]

    print("Starting A2RCHI deployment process...")
    setup_cli_logging(verbosity=verbosity)
    logger = get_logger(__name__)
    
    try:
        # Validate inputs
        validate_services_selection(services)
        
        # Combine services and data sources for processing
        enabled_services = services.copy()
        enabled_services.extend(sources)
        
        # Log deployment info and dependency resolution
        log_deployment_start(name, services, sources, dry)
        added_services = log_dependency_resolution(services, enabled_services)
        enabled_services += list(added_services)

        
        # Handle existing deployment
        base_dir = Path(A2RCHI_DIR) / f"a2rchi-{name}"
        handle_existing_deployment(base_dir, name, force, dry, other_flags.get('podman', False))
        
        # Initialize managers
        logger.info(f"current enabled services: {enabled_services}")
        gpu = other_flags.get("gpu-ids") != None
        config_manager = MultiConfigManager(configs, enabled_services, gpu, env)
        logger.info("Configuration validated successfully")
        secrets_manager = SecretsManager(env_file, config_manager)
        
        required_secrets, all_secrets = secrets_manager.get_secrets(set(enabled_services))
        secrets_manager.validate_secrets(required_secrets)
        logger.info(f"Required secrets validated: {', '.join(sorted(required_secrets))}")
        extra = all_secrets - required_secrets
        if extra:
            logger.info(f"Also passing additional secrets found: {', '.join(sorted(extra))}")
        
        # Build compose configuration
        compose_config = ServiceBuilder.build_compose_config(
            name=name, verbosity=verbosity, base_dir=base_dir,
            enabled_services=enabled_services, secrets=all_secrets,
            **other_flags
        )

        # Handle dry run
        if dry:
            service_only_resolved = [s for s in service_registry.resolve_dependencies(enabled_services) 
                                   if s in service_registry.get_all_services()]
            print_dry_run_summary(name, services, service_only_resolved, sources, 
                                 required_secrets, compose_config, other_flags, base_dir)
            return
        
        # Actual deployment
        template_manager = MultiTemplateManager(env, config_manager)
        base_dir.mkdir(parents=True, exist_ok=True)
        
        secrets_manager.write_secrets_to_files(base_dir, all_secrets)
        
        volume_manager = VolumeManager(compose_config.use_podman)
        volume_manager.create_required_volumes(compose_config)
        
        # again we should only be passing in the config manager here 
        template_manager.prepare_deployment_files(compose_config, secrets_manager, **other_flags)
        
        deployment_manager = DeploymentManager(compose_config.use_podman)
        deployment_manager.start_deployment(base_dir)
        
        # Log success
        service_only_resolved = [s for s in service_registry.resolve_dependencies(enabled_services) 
                               if s in service_registry.get_all_services()]
        log_deployment_success(name, service_only_resolved, services, config_manager)
        
    except Exception as e:
        if verbosity >= 4:
            traceback.print_exc()
        else:
            raise click.ClickException(str(e))
    

@click.command()
@click.option('--name', '-n', type=str, help="Name of the a2rchi deployment to delete")
@click.option('--rmi', is_flag=True, help="Remove images (--rmi all)")
@click.option('--rmv', is_flag=True, help="Remove volumes (--volumes)")
@click.option('--keep-files', is_flag=True, help="Keep deployment files (don't remove directory)")
@click.option('--list', 'list_deployments', is_flag=True, help="List all available deployments")
@click.option('--verbosity', '-v', type=int, default=3, help="Logging verbosity level (0-4)")
def delete(name: str, rmi: bool, rmv: bool, keep_files: bool, list_deployments: bool, verbosity: int):
    """
    Delete an A2RCHI deployment with the specified name.
    
    This command stops containers and optionally removes images, volumes, and files.
    
    Examples:
    
    # List available deployments
    a2rchi delete --list
    
    # Delete deployment (keep images and volumes)
    a2rchi delete --name mybot
    
    # Delete deployment and remove images
    a2rchi delete --name mybot --rmi
    
    # Complete cleanup (remove everything)
    a2rchi delete --name mybot --rmi --rmv
    
    # Stop deployment but keep files for debugging
    a2rchi delete --name mybot --keep-files
    """
    
    setup_cli_logging(verbosity=verbosity)
    logger = get_logger(__name__)

    try:
        # We don't know which tool was used to create it, so try to detect from files
        deployment_manager = DeploymentManager(use_podman=True)  # Will try both tools
        
        # Handle list option
        if list_deployments:
            deployments = deployment_manager.list_deployments()
            if deployments:
                logger.info("Available deployments:")
                for deployment in deployments:
                    logger.info(f"  - {deployment}")
            else:
                logger.info("No deployments found")
            return
        
        # Validate name is provided
        if not name:
            available = deployment_manager.list_deployments()
            if available:
                available_str = ", ".join(available)
                raise click.ClickException(
                    f"Please provide a deployment name using --name.\n"
                    f"Available deployments: {available_str}\n"
                    f"Use 'a2rchi delete --list' to see all deployments."
                )
            else:
                raise click.ClickException(
                    "Please provide a deployment name using --name.\n"
                    "No deployments found. Use 'a2rchi create' to create one."
                )
        
        # Clean the name
        name = name.strip()
        
        # Confirm deletion if removing volumes
        if rmv:
            click.confirm(
                f"This will permanently delete volumes for deployment '{name}'. Continue?",
                abort=True
            )
        
        # Perform deletion using DeploymentManager
        deployment_manager.delete_deployment(
            deployment_name=name,
            remove_images=rmi,
            remove_volumes=rmv,
            remove_files=not keep_files
        )
        
    except Exception as e:
        traceback.print_exc()
        raise click.ClickException(str(e))
    
@click.command()
def list_services():
    """List all available services"""
    
    click.echo("Available A2RCHI services:\n")
    
    # Application services
    app_services = service_registry.get_application_services()
    if app_services:
        click.echo("Application Services:")
        for name, service_def in app_services.items():
            click.echo(f"  {name:20} {service_def.description}")
        click.echo()
    
    # Integration services
    integration_services = service_registry.get_integration_services()
    if integration_services:
        click.echo("Integration Services:")
        for name, service_def in integration_services.items():
            click.echo(f"  {name:20} {service_def.description}")
        click.echo()
    
    # Data sources
    click.echo("Data Sources:")
    click.echo("  redmine              Redmine issue tracking integration")
    click.echo("  jira                 Jira issue tracking integration")


@click.command()
def list_deployments():
    """List all existing deployments"""
    
    a2rchi_dir = Path(A2RCHI_DIR)

    if not a2rchi_dir.exists():
        click.echo("No deployments found")
        return
    
    deployments = [d for d in a2rchi_dir.iterdir() 
                  if d.is_dir() and d.name.startswith('a2rchi-')]
    
    if not deployments:
        click.echo("No deployments found")
        return
    
    click.echo("Existing deployments:")
    for deployment in deployments:
        name = deployment.name.replace('a2rchi-', '')
        
        # Try to get running status
        try:
            compose_file = deployment / "compose.yaml"
            if compose_file.exists():
                click.echo(f"  {name}")
            else:
                click.echo(f"  {name} (incomplete)")
        except Exception:
            click.echo(f"  {name} (status unknown)")

@click.command()
@click.option('--name', '-n', type=str, required=True, help="Name of the a2rchi deployment")
@click.option('--config', '-c', 'config_files', type=str, help="Path to .yaml a2rchi configuration")
@click.option('--config-dir', '-cd', 'config_dir', type=str, help="Path to configs directory")
@click.option('--env-file', '-e', type=str, required=True, help="Path to .env file with 'secrets")
@click.option('--sources', '-src', callback=parse_sources_option,
              help="Comma-separated list of data sources: jira,redmine")
@click.option('--podman', '-p', is_flag=True, help="Use Podman instead of Docker")
@click.option('--gpu-ids', callback=parse_gpu_ids_option, help='GPU configuration: "all" or comma-separated IDs')
@click.option('--force', '-f', is_flag=True, help="Force deployment creation, overwriting existing deployment")
@click.option('--tag', '-t', type=str, default="2000", help="Image tag for built containers")
@click.option('--verbosity', '-v', type=int, default=3, help="Logging verbosity level (0-4)")
def evaluate(name: str, config_files: str, config_dir: str, env_file: str, sources: list, 
             force: bool, verbosity: int, **other_flags):
    """Create an A2RCHI deployment with selected services and data sources."""
    # note this has not yet been made to work with the sources, i need to get on that

    if not (bool(config_files) ^ bool(config_dir)): 
        raise click.ClickException(f"Must specify only one of config files or config dir")
    if config_dir: 
        config_path = Path(config_dir)
        configs = [Path(item) for item in config_path.iterdir() if item.is_file()]
    else: 
        def safe_get_path(item): 
            res = Path(item)
            if not res.exists():    
                raise Exception(f"Failed to get config path for file {item}")
            return res 



        configs = [safe_get_path(item) for item in config_files.split(",")]

    print("Starting A2RCHI benchmarking process...")
    setup_cli_logging(verbosity=verbosity)
    logger = get_logger(__name__)

    gpu = other_flags.get("gpu-ids") != None

    try: 
        base_dir = Path(A2RCHI_DIR) / f"a2rchi-{name}"
        handle_existing_deployment(base_dir, name, force, False, other_flags.get('podman', False))

        enabled_services = ["chromadb", "postgres", "benchmarking"] 

        if base_dir.exists():
            raise click.ClickException(
                    f"Benchmarking runtime '{name}' already exists at {base_dir}"
                    )

        config_manager = MultiConfigManager(configs, enabled_services, gpu, env)
        
        # the benchmarking config  manager is duck typed to look like a regular config manager so its chill
        secrets_manager = SecretsManager(env_file, config_manager)
        _, all_secrets = secrets_manager.get_secrets(set())

        service_configs = config_manager.get_service_configs()

        other_flags['benchmarking'] = True
        print(f"current service configs: {service_configs}")
        other_flags['query_file'] = service_configs['benchmarking'].get('queries_path', ".")
        other_flags['benchmarking_dest'] = os.path.abspath(service_configs['benchmarking'].get('out_dir', '.'))
        other_flags['host_mode'] = False

        compose_config = ServiceBuilder.build_compose_config(
                name=name, verbosity=verbosity, base_dir=base_dir, 
                enabled_services=enabled_services, secrets=all_secrets,
                **other_flags
                )


        # change over to MultiTemplateManager when you later test this
        template_manager = MultiTemplateManager(env, config_manager)
        base_dir.mkdir(parents=True, exist_ok=True)
        
        # make the additional files needed for evaluation
        secrets_manager.write_secrets_to_files(base_dir, all_secrets)

        volume_manager = VolumeManager(compose_config.use_podman)
        volume_manager.create_required_volumes(compose_config)
        
        template_manager.prepare_deployment_files(compose_config, secrets_manager, **other_flags)

        deployment_manager = DeploymentManager(compose_config.use_podman)
        deployment_manager.start_deployment(base_dir)
    except Exception as e:
        if verbosity >=4: 
            traceback.print_exc()
        else: 
            raise click.ClickException(f"Failed due to the following exception: {e}")

def main():
    """
    Entrypoint for a2rchi cli tool implemented using Click.
    """
    # cli.add_command(help)
    cli.add_command(create)
    cli.add_command(delete)
    cli.add_command(list_services)
    cli.add_command(list_deployments)
    cli.add_command(evaluate)
    cli()
