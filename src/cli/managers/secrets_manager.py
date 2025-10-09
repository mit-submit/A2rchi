from pathlib import Path
from typing import List, Set, Tuple

from dotenv import dotenv_values

from src.cli.service_registry import service_registry
from src.cli.source_registry import source_registry
from src.utils.logging import get_logger

logger = get_logger(__name__)

class SecretsManager:
    """Manages secret loading and validation using .env files"""

    def __init__(self, env_file_path: str = None, config_manager = None):
        if not env_file_path:
            env_file_path = "src/cli/managers/secrets_dummy.env"
            logger.warning(f"No .env file specified, defaulting to a dummy .env file with only PG_PASSWORD: {env_file_path}.")
        
        self.env_file_path = Path(env_file_path)
        if not self.env_file_path.exists():
            raise FileNotFoundError(f"Environment file not found: {self.env_file_path}")
        
        self.registry = service_registry
        self.secrets = self._load_env_file()

        self.config_manager = config_manager

    def _load_env_file(self) -> None:
        """Load secrets from .env file"""
        try:
            # dotenv_values return a dict
            secrets = dotenv_values(self.env_file_path)

            return {k: v for k, v in secrets.items() if v is not None and v.strip()}

        except Exception as e:
            raise ValueError(f"Error parsing .env file {self.env_file_path}: {e}")
        
    def get_secrets(self, services: Set[str], sources: Set[str]) -> Tuple[Set[str], Set[str]]:
        """Return both secrets required by services/sources and the full list"""
        required = self.get_required_secrets_for_services(services) | self.get_required_secrets_for_sources(sources)
        all_secrets = set(self.secrets.keys())
        return required, all_secrets

    def get_required_secrets_for_services(self, services: Set[str]) -> Set[str]:
        """Determine required secrets based on configuration and enabled services"""
        required_secrets = set()

        # always required
        required_secrets.add("PG_PASSWORD")

        # LLM
        model_secrets = self._get_model_based_secrets()
        required_secrets.update(model_secrets)

        # embeddings
        embedding_secrets = self._extract_embedding_secrets()
        required_secrets.update(embedding_secrets)

        # registry (service) secrets
        registry_secrets = self.registry.get_required_secrets(list(services))
        required_secrets.update(registry_secrets)

        return required_secrets

    def get_required_secrets_for_sources(self, sources: Set[str]) -> Set[str]:
        if not sources:
            return set()
        return set(source_registry.required_secrets(list(sources)))

    def _get_model_based_secrets(self) -> Set[str]:
        """Extract required secrets based on models being used for selected pipeline"""
        model_secrets = set()

        models_configs = self.config_manager.get_models_configs()

        for models_config in models_configs:
            for _ , section_models in models_config.items():
                if not isinstance(section_models, dict):
                    continue
                for _ , model_name in section_models.items():
                    if "OpenAI" in model_name:
                        model_secrets.add("OPENAI_API_KEY")
                    elif "Anthropic" in model_name:
                        model_secrets.add("ANTHROPIC_API_KEY")
                    elif "HuggingFace" in model_name or "Llama" in model_name or "VLLM" in model_name:
                        logger.warning("You are using open source models; make sure to include a HuggingFace token if required for usage, it won't be explicitly enforced")
                
        logger.debug(f"Required model secrets: {model_secrets or 'None'}")
        return model_secrets

    def _extract_embedding_secrets(self) -> Set[str]:
        """Extract required secrets for embedding models"""
        embedding_secrets = set()
        configs = self.config_manager.get_configs()
        
        for config in configs:
            embedding_name = config.get("data_manager", {}).get("embedding_name", "")
            if "OpenAI" in embedding_name:
                embedding_secrets.add("OPENAI_API_KEY")
            elif "HuggingFace" in embedding_name:
                logger.warning("You are using an embedding model from HuggingFace; make sure to include a HuggingFace token if required for usage, it won't be explicitly enforced")
        
        return embedding_secrets
    
    def validate_secrets(self, required_secrets: Set[str]) -> None:
        """Validate that all required secrets are available in the .env file"""
        missing_secrets = set()

        for secret in required_secrets:
            if secret not in self.secrets:
                missing_secrets.add(secret)

        if missing_secrets:
            raise ValueError(
                f"Missing required secrets in {self.env_file_path}:\n"
                f"  {', '.join(missing_secrets)}\n\n"
                f"Please add these to your .env file in the format:\n"
                f"SECRET_NAME=secret_value\n\n"
                f"Example .env file:\n"
                f"PG_PASSWORD=mysecretpassword123\n"
                f"OPENAI_API_KEY=sk-...\n"
                f"GRAFANA_PG_PASSWORD=grafana123\n"
            )
        
    def get_secret(self, key: str) -> str:
        """Get a secret value by exact key match"""
        if key not in self.secrets:
            raise KeyError(f"Secret '{key}' not found in .env file")
        return self.secrets[key]
    
    def write_secrets_to_files(self, target_dir: Path, secrets: Set[str]) -> None:
        """Write required secrets to individual files in the target directory"""
        secrets_dir = target_dir / "secrets"
        secrets_dir.mkdir(exist_ok=True)

        for secret_name in secrets:
            try:
                secret_value = self.get_secret(secret_name)
                # lowercase for compose
                secret_file = secrets_dir / f"{secret_name.lower()}.txt"
                with open(secret_file, 'w') as f:
                    f.write(secret_value)
            except KeyError:
                # should never happen if validate_secrets() was called first...
                raise ValueError(f"Secret '{secret_name}' is required but not found in .env file")
            
    def list_available_secrets(self) -> List[str]:
        """List all secrets available in the .env file (for debugging)"""
        return list(self.secrets.keys())
    
    def get_env_file_path(self) -> Path:
        """Get the path to the .env file being used"""
        return self.env_file_path