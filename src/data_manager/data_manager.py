import os
from typing import Callable, Optional

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.tickets.ticket_manager import TicketManager
from src.data_manager.collectors.localfile_manager import LocalFileManager
from src.data_manager.vectorstore.manager import VectorStoreManager
from src.utils.config_access import get_full_config
from src.utils.config_service import ConfigService
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

class DataManager():

    def __init__(self, *, run_ingestion: bool = True, factory=None):

        self.config = get_full_config()
        self.global_config = self.config["global"]
        self.data_path = self.global_config["DATA_PATH"]
        self.should_run_ingestion = run_ingestion

        os.makedirs(self.data_path, exist_ok=True)

        self.pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **self.config["services"]["postgres"],
        }
        self.persistence = PersistenceService(self.data_path, pg_config=self.pg_config)
        self.config_service = factory.config_service if factory else ConfigService(pg_config=self.pg_config)
        static_config = self.config_service.get_static_config()
        if not static_config or static_config.sources_config is None:
            raise RuntimeError("Static config missing sources_config; run deployment initialization first.")
        self.config["data_manager"]["sources"] = static_config.sources_config

        self.localfile_manager = LocalFileManager(dm_config=self.config["data_manager"])
        self.scraper_manager = ScraperManager(dm_config=self.config["data_manager"])
        self.ticket_manager = TicketManager(dm_config=self.config["data_manager"])

        captioning_cfg = self.config["data_manager"].get("captioning", {})
        if captioning_cfg.get("enabled", False):
            self._resolve_captioning_provider(captioning_cfg)

        self.vector_manager = VectorStoreManager(
            config=self.config,
            global_config=self.global_config,
            data_path=self.data_path,
            pg_config=self.pg_config,
        )

        self.collection_name = self.vector_manager.collection_name
        self.distance_metric = self.vector_manager.distance_metric
        self.embedding_model = self.vector_manager.embedding_model
        self.text_splitter = self.vector_manager.text_splitter
        self.stemmer = self.vector_manager.stemmer

        logger.info(f"Using collection: {self.collection_name}")

        if self.should_run_ingestion:
            self.run_ingestion()

    def run_ingestion(self, progress_callback: Optional[Callable[[str], None]] = None) -> None:
        """Execute initial ingestion and vectorstore update."""
        source_aggregation = [
            ("Copying configured local files", lambda: self.localfile_manager.collect_all_from_config(self.persistence)),
            ("Scraping documents onto filesystem", lambda: self.scraper_manager.collect_all_from_config(self.persistence)),
            ("Fetching ticket data onto filesystem", lambda: self.ticket_manager.collect_all_from_config(self.persistence)),
        ]

        for message, step in source_aggregation:
            logger.info(message)
            if progress_callback:
                progress_callback(message)
            step()

        if progress_callback:
            progress_callback("Flushing indices")
        self.persistence.flush_index()


        # Verify catalog was updated
        catalog = self.persistence.catalog
        catalog.refresh()  # Ensure we have the latest data
        logger.info(f"Catalog contains {len(catalog.file_index)} resources after flush")

        if progress_callback:
            progress_callback("Resetting vectorstore (if configured)")
        self.vector_manager.delete_existing_collection_if_reset()
        if progress_callback:
            progress_callback("Updating vectorstore")
        self.vector_manager.update_vectorstore()

    def delete_existing_collection_if_reset(self, *, force: bool = False):
        """Proxy to the underlying vector manager."""
        if not (self.should_run_ingestion or force):
            logger.debug("Skipping collection reset check (ingestion disabled).")
            return None
        return self.vector_manager.delete_existing_collection_if_reset()

    def fetch_collection(self):
        """Proxy to the underlying vector manager."""
        return self.vector_manager.fetch_collection()

    def update_vectorstore(self, *, force: bool = False):
        """Proxy to the underlying vector manager."""
        if not (self.should_run_ingestion or force):
            logger.debug("Skipping vectorstore update (ingestion disabled).")
            return None
        self.vector_manager.update_vectorstore()

    def _update_after_collect(self) -> None:
        self.persistence.flush_index()
        self.vector_manager.update_vectorstore()

    def _resolve_captioning_provider(self, captioning_cfg: dict) -> None:
        """Resolve the configured captioning provider for ingestion."""
        from src.data_manager.captioning.caption_service import ConfigurationError

        provider_name = captioning_cfg.get("provider")
        if not provider_name:
            raise ConfigurationError(
                "captioning.enabled is true but captioning.provider is not set."
            )

        base_url = captioning_cfg.get("base_url")
        if not base_url:
            services_cfg = self.config.get("services", {})
            for service_cfg in services_cfg.values():
                if not isinstance(service_cfg, dict):
                    continue
                provider_cfg = service_cfg.get("providers", {})
                if not isinstance(provider_cfg, dict):
                    continue
                entry = provider_cfg.get(provider_name, {})
                if isinstance(entry, dict) and entry.get("base_url"):
                    base_url = entry["base_url"]
                    break

        try:
            from src.archi.providers import get_provider, get_provider_by_name
            from src.archi.providers.base import ProviderConfig, ProviderType

            if base_url and provider_name.lower() in ("local", "ollama"):
                config = ProviderConfig(
                    provider_type=ProviderType.LOCAL,
                    base_url=base_url,
                    models=[],
                )
                provider = get_provider(ProviderType.LOCAL, config=config, use_cache=False)
            else:
                provider = get_provider_by_name(provider_name)
        except ImportError:
            logger.warning(
                "Provider registry not available; attempting direct provider resolution."
            )
            provider = self._resolve_provider_direct(provider_name, base_url=base_url)

        if provider is None:
            raise ConfigurationError(
                f"Could not resolve captioning provider '{provider_name}'. "
                "Check that the provider is configured and has valid credentials."
            )

        captioning_cfg["_provider"] = provider
        logger.info("Resolved captioning provider: %s", provider_name)

    def _resolve_provider_direct(self, provider_name: str, base_url: Optional[str] = None):
        """Fallback provider resolution without the registry helper."""
        import importlib

        from src.archi.providers.base import ProviderConfig, ProviderType

        provider_type_map = {
            "openai": (
                "src.archi.providers.openai_provider",
                "OpenAIProvider",
                ProviderType.OPENAI,
                "OPENAI_API_KEY",
            ),
            "anthropic": (
                "src.archi.providers.anthropic_provider",
                "AnthropicProvider",
                ProviderType.ANTHROPIC,
                "ANTHROPIC_API_KEY",
            ),
            "gemini": (
                "src.archi.providers.gemini_provider",
                "GeminiProvider",
                ProviderType.GEMINI,
                "GOOGLE_API_KEY",
            ),
            "openrouter": (
                "src.archi.providers.openrouter_provider",
                "OpenRouterProvider",
                ProviderType.OPENROUTER,
                "OPENROUTER_API_KEY",
            ),
            "local": (
                "src.archi.providers.local_provider",
                "LocalProvider",
                ProviderType.LOCAL,
                "",
            ),
            "cern_litellm": (
                "src.archi.providers.cern_litellm_provider",
                "CERNLiteLLMProvider",
                ProviderType.CERN_LITELLM,
                "",
            ),
        }

        entry = provider_type_map.get(provider_name.lower())
        if entry is None:
            return None

        module_path, class_name, provider_type, api_key_env = entry
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        if not base_url:
            services_cfg = self.config.get("services", {})
            for service_cfg in services_cfg.values():
                if not isinstance(service_cfg, dict):
                    continue
                entry_cfg = service_cfg.get("providers", {}).get(provider_name, {})
                if isinstance(entry_cfg, dict) and entry_cfg.get("base_url"):
                    base_url = entry_cfg["base_url"]
                    break

        config = ProviderConfig(
            provider_type=provider_type,
            api_key_env=api_key_env,
            base_url=base_url,
            enabled=True,
        )
        try:
            return cls(config)
        except Exception as exc:
            logger.warning("Failed to create provider %s: %s", provider_name, exc)
            return None
