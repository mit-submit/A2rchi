import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from typing import Tuple

from src.cli.utils.command_runner import CommandRunner
from src.utils.logging import get_logger

logger = get_logger(__name__)

class DeploymentError(Exception):
    """Custom exception for deployment failures"""
    def __init__(self, message: str, exit_code: int, stderr: str = None):
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(message)

class DeploymentManager:
    """Manages container deployment using Compose"""
    
    def __init__(self, use_podman: bool = False):
        self.use_podman = use_podman
        self.compose_tool = "podman compose" if use_podman else "docker compose"
    
    def start_deployment(self, deployment_dir: Path) -> None:
        """Start the deployment using compose"""
        compose_file = deployment_dir / "compose.yaml"
        
        if not compose_file.exists():
            raise FileNotFoundError(f"Compose file not found: {compose_file}")
        
        logger.info(f"Starting compose deployment from {deployment_dir}")
        logger.info(f"Using compose file: {compose_file}")
        logger.info(f"(This might take a minute...)")
        
        # Validate compose file syntax first
        try:
            self._validate_compose_file(compose_file)
        except Exception as e:
            raise DeploymentError(f"Invalid compose file: {e}", 1)
        
        flags = os.environ.get("A2RCHI_COMPOSE_UP_FLAGS", "--build --force-recreate --always-recreate-deps")
        compose_cmd = f"{self.compose_tool} -f {compose_file} up -d {flags}"
        
        try:
            stdout, stderr, exit_code = CommandRunner.run_streaming(compose_cmd, cwd=deployment_dir)
            
            if exit_code != 0:
                error_msg = f"Deployment failed with exit code {exit_code}"
                if stderr.strip():
                    error_msg += f"\nError output:\n{stderr}"
                raise DeploymentError(error_msg, exit_code, stderr)
            
            logger.info("Deployment started successfully")
            
        except KeyboardInterrupt:
            logger.warning("Deployment interrupted by user")
            raise
        except subprocess.SubprocessError as e:
            raise DeploymentError(f"Failed to execute compose command: {e}", getattr(e, 'returncode', 1))
    
    def stop_deployment(self, deployment_dir: Path) -> None:
        """Stop the deployment"""
        compose_file = deployment_dir / "compose.yaml"
        
        if not compose_file.exists():
            raise FileNotFoundError(f"Compose file not found: {compose_file}")
        
        logger.info("Stopping deployment")
        
        compose_cmd = f"{self.compose_tool} -f {compose_file} down"
        
        try:
            stdout, stderr, exit_code = CommandRunner.run_streaming(compose_cmd, cwd=deployment_dir)
            
            if exit_code != 0:
                logger.warning(f"Stop command completed with exit code {exit_code}")
                if stderr.strip():
                    logger.warning(f"Warning output:\n{stderr}")
            else:
                logger.info("Deployment stopped successfully")
                
        except subprocess.SubprocessError as e:
            raise DeploymentError(f"Failed to stop deployment: {e}", getattr(e, 'returncode', 1))
    
    def delete_deployment(self, deployment_name: str, remove_images: bool = False, 
                         remove_volumes: bool = False, remove_files: bool = True) -> None:
        """Delete a deployment and optionally clean up resources"""
        # Determine deployment directory
        import os

        from src.cli.managers.volume_manager import VolumeManager
        A2RCHI_DIR = os.environ.get('A2RCHI_DIR', os.path.join(os.path.expanduser('~'), ".a2rchi"))
        deployment_dir = Path(A2RCHI_DIR) / f"a2rchi-{deployment_name}"
        
        if deployment_dir.exists():
            # Stop deployment first
            try:
                self.stop_deployment(deployment_dir)
            except Exception as e:
                logger.warning(f"Could not stop deployment: {e}")
            
            # Remove images if requested
            if remove_images:
                try:
                    self._remove_images(deployment_dir)
                except Exception as e:
                    logger.warning(f"Could not remove images: {e}")
            
            # Remove volumes if requested
            if remove_volumes:
                try:
                    volume_manager = VolumeManager(self.use_podman)
                    volume_manager.remove_deployment_volumes(deployment_name, force=True)
                except Exception as e:
                    logger.warning(f"Could not remove volumes: {e}")
            
            # Remove files if requested
            if remove_files:
                try:
                    import shutil
                    shutil.rmtree(deployment_dir)
                    logger.info(f"Removed deployment directory: {deployment_dir}")
                except Exception as e:
                    logger.warning(f"Could not remove deployment directory: {e}")
    
    def _validate_compose_file(self, compose_file: Path) -> None:
        """Validate compose file syntax"""
        try:
            import yaml
            with open(compose_file, 'r') as f:
                yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML syntax error in compose file: {e}")
        except Exception as e:
            raise ValueError(f"Could not read compose file: {e}")
    
    def _remove_images(self, deployment_dir: Path) -> None:
        """Remove images associated with the deployment"""
        compose_file = deployment_dir / "compose.yaml"
        if not compose_file.exists():
            return
            
        # Get list of images
        images_cmd = f"{self.compose_tool} -f {compose_file} images -q"
        try:
            stdout, stderr, exit_code = CommandRunner.run_streaming(images_cmd, cwd=deployment_dir)
            if exit_code == 0 and stdout.strip():
                # Remove images
                tool = "podman" if self.use_podman else "docker"
                for image_id in stdout.strip().split('\n'):
                    if image_id.strip():
                        remove_cmd = f"{tool} rmi {image_id.strip()}"
                        CommandRunner.run_streaming(remove_cmd, cwd=deployment_dir)
                        logger.info(f"Removing image with id: {image_id}")
        except Exception as e:
            logger.warning(f"Could not remove images: {e}")