from a2rchi.cli.utils.command_runner import CommandRunner
from a2rchi.utils.logging import get_logger
from typing import Tuple

import shlex
import subprocess

logger = get_logger(__name__)

class VolumeManager:
    """Manages Docker/Podman volume creation"""
    
    def __init__(self, use_podman: bool = False):
        self.use_podman = use_podman
        self.container_tool = "podman" if use_podman else "docker"
    
    def create_required_volumes(self, compose_config) -> None:
        """Create all volumes required by the deployment"""
        required_volumes = compose_config.get_required_volumes()
        
        for volume_name in required_volumes:
            self._create_volume(volume_name)
    
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
        
        stdout, stderr, _ = CommandRunner.run_simple(create_cmd)
        if stderr:
            raise RuntimeError(f"Failed to create volume '{volume_name}': {stderr}")
        
    def remove_volume(self, volume_name_substr: str, force: bool = False) -> None:
        """Remove any volume containing the given substring"""
        logger.info(f"Looking for volumes containing: '{volume_name_substr}'")

        # List existing volumes
        list_cmd = "podman volume ls --format '{{.Name}}'" if self.use_podman else "docker volume ls --format '{{.Name}}'"
        stdout, stderr, _ = CommandRunner.run_simple(list_cmd)

        # Podman prints a warning to stderr even when it succeeds
        if stderr and "Emulate Docker CLI using podman" not in stderr:
            raise RuntimeError(f"Failed to list volumes: {stderr}")

        all_volumes = stdout.strip().splitlines()
        matching_volumes = [v for v in all_volumes if volume_name_substr in v]

        if not matching_volumes:
            logger.info(f"No volumes matching '{volume_name_substr}' found. Nothing to remove.")
            return

        for vol in matching_volumes:
            logger.info(f"Removing volume: {vol}")
            remove_cmd = f"podman volume rm {'-f' if force else ''} {vol}" if self.use_podman else f"docker volume rm {'-f' if force else ''} {vol}"
            stdout, stderr, _ = CommandRunner.run_simple(remove_cmd)
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
        stdout, stderr, _ = CommandRunner.run_simple(list_cmd)
        
        if stderr:
            raise RuntimeError(f"Failed to list volumes: {stderr}")
        
        # Check if volume name appears in the output
        for line in stdout.split("\n"):
            if volume_name in line:
                return True
        return False