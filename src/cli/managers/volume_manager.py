from typing import Optional

from src.cli.utils.command_runner import CommandRunner
from src.cli.utils.local_file_stager import stage_local_files_to_volume
from src.utils.logging import get_logger

logger = get_logger(__name__)

class VolumeManager:
    """Manages Docker/Podman volume creation"""
    
    def __init__(self, use_podman: bool = False):
        self.use_podman = use_podman
        self.container_tool = "podman" if use_podman else "docker"
    
    def create_required_volumes(self, compose_config, config: Optional[dict] = None) -> None:
        """Create all volumes required by the deployment and stage local files when configured."""
        required_volumes = compose_config.get_required_volumes()
        
        for volume_name in required_volumes:
            self._create_volume(volume_name)
        
        if config is not None:
            self._stage_local_files(compose_config, config)
    
    def _create_volume(self, volume_name: str) -> None:
        """Create a single volume if it doesn't exist"""
        # Check if volume already exists
        if self._volume_exists(volume_name):
            logger.info(f"Volume '{volume_name}' already exists. No action needed.")
            return
        
        # Create the volume
        logger.info(f"Creating volume: {volume_name}")
        if self.use_podman:
            create_cmd = f"podman volume create {volume_name}"
        else:
            create_cmd = f"docker volume create --name {volume_name}"
        
        stdout, stderr, exit_code = CommandRunner.run_simple(create_cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to create volume '{volume_name}': {stderr}")
        elif stderr:
            logger.warning(f"Volume creation warning: {stderr.strip()}")
        
    def remove_volume(self, volume_name_substr: str, force: bool = False) -> None:
        """Remove any volume that ends with '-<name>'"""
        logger.info(f"Looking for volumes ending with '-{volume_name_substr}'")

        # List existing volumes
        list_cmd = "podman volume ls --format '{{.Name}}'" if self.use_podman else "docker volume ls --format '{{.Name}}'"
        stdout, stderr, exit_code = CommandRunner.run_simple(list_cmd)

        # Podman can print warnings to stderr even when it succeeds
        if exit_code != 0:
            raise RuntimeError(f"Failed to list volumes: {stderr}")
        elif stderr:
            logger.warning(f"Volume listing warning: {stderr.strip()}")

        all_volumes = stdout.strip().splitlines()
        suffix = f"-{volume_name_substr}"
        matching_volumes = [v for v in all_volumes if v.endswith(suffix)]

        if not matching_volumes:
            logger.info(f"No volumes matching '{volume_name_substr}' found. Nothing to remove.")
            return

        for vol in matching_volumes:
            logger.info(f"Removing volume: {vol}")
            remove_cmd = f"podman volume rm {'-f' if force else ''} {vol}" if self.use_podman else f"docker volume rm {'-f' if force else ''} {vol}"
            stdout, stderr, exit_code = CommandRunner.run_simple(remove_cmd)
            if stderr and "Emulate Docker CLI using podman" not in stderr:
                logger.warning(f"Failed to remove volume '{vol}': {stderr}")
            else:
                logger.info(stdout.strip())

    def remove_deployment_volumes(self, deployment_name: str, force: bool = False) -> None:
        """Remove all volumes containing the deployment name"""
        self.remove_volume(deployment_name, force=force)

    def _volume_exists(self, volume_name: str) -> bool:
        """Check if a volume already exists"""
        list_cmd = f"{self.container_tool} volume ls"
        stdout, stderr, exit_code = CommandRunner.run_simple(list_cmd)
        
        if exit_code != 0:
            raise RuntimeError(f"Failed to list volumes: {stderr}")
        elif stderr:
            logger.warning(f"Volume listing warning: {stderr.strip()}")
        
        # Check if volume name appears in the output
        for line in stdout.split("\n"):
            if volume_name in line:
                return True
        return False

    def _stage_local_files(self, compose_config, config: dict) -> None:
        """Stage local files into the data-manager volume if configured."""
        try:
            data_mgr_service = compose_config.get_service("data-manager")
        except Exception as exc:
            logger.warning("Unable to inspect data-manager service for staging: %s", exc)
            return

        if not data_mgr_service.enabled or not data_mgr_service.volume_name:
            return

        try:
            stage_local_files_to_volume(
                config=config,
                volume_name=data_mgr_service.volume_name,
                container_tool=self.container_tool,
            )
        except Exception as exc:
            logger.warning("Failed to stage local_files into volume: %s", exc)
