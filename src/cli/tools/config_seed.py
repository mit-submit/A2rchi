"""
One-shot config seeder for compose deployments.

Expects env:
- PGHOST, PGPORT, PGDATABASE, PGUSER, PG_PASSWORD
- CONFIG_PATH: path to rendered config.yaml inside container

Actions:
1) Ensure schema/columns via ConfigService (it will apply DDL best-effort).
2) Upsert static_config from YAML.
3) Initialize dynamic_config only if empty.
Exits 0 on success, non-zero on failure.
"""

import os
import sys
import yaml
from typing import Any, Dict, Optional

from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.config_service import ConfigService


def load_config(path: str):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _copy_mapping(value):
    if isinstance(value, dict):
        return {k: _copy_mapping(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_mapping(item) for item in value]
    return value


def _resolve_seeded_services_config(
    services: Dict[str, Any],
    existing_services: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    seeded_services = _copy_mapping(services or {})
    chat_cfg = seeded_services.setdefault("chat_app", {})
    yaml_ab_cfg = chat_cfg.get("ab_testing")
    existing_chat_cfg = ((existing_services or {}).get("chat_app") or {})
    existing_ab_cfg = existing_chat_cfg.get("ab_testing")

    if not isinstance(yaml_ab_cfg, dict):
        if isinstance(existing_ab_cfg, dict) and existing_ab_cfg:
            chat_cfg["ab_testing"] = _copy_mapping(existing_ab_cfg)
        return seeded_services

    force_yaml_override = bool(yaml_ab_cfg.get("force_yaml_override", False))
    yaml_ab_cfg = {k: _copy_mapping(v) for k, v in yaml_ab_cfg.items() if k != "force_yaml_override"}
    chat_cfg["ab_testing"] = yaml_ab_cfg

    if isinstance(existing_ab_cfg, dict) and existing_ab_cfg and not force_yaml_override:
        chat_cfg["ab_testing"] = _copy_mapping(existing_ab_cfg)

    return seeded_services


def seed(config: dict, cs: ConfigService):
    print("[config-seed] Starting seed with config keys:", list(config.keys()))
    dm = config.get("data_manager", {})
    services = config.get("services", {})
    archi_cfg = config.get("archi", {}) or {}
    mcp_servers = config.get("mcp_servers", {}) or {}
    archi_cfg = {**archi_cfg}
    global_cfg = config.get("global", {})
    current_static = cs.get_static_config(force_reload=True)
    seeded_services = _resolve_seeded_services_config(
        services,
        current_static.services_config if current_static else None,
    )

    # Embedding dimensions fallback TODO why is this here?
    embedding_name = dm.get("embedding_name", "HuggingFaceEmbeddings")
    embedding_class_map = dm.get("embedding_class_map", {})
    embedding_dimensions = embedding_class_map.get(embedding_name, {}).get("dimensions", 384)

    agent_class = seeded_services.get("chat_app", {}).get("agent_class")
    provider = seeded_services.get("chat_app", {}).get("provider")
    model = seeded_services.get("chat_app", {}).get("model")
    available_pipelines = [agent_class] if agent_class else []
    available_models = [f"{provider}/{model}"] if provider and model else []
    available_providers = [provider] if provider else []

    cs.initialize_static_config(
        deployment_name=config.get("name", "default"),
        data_path=global_cfg.get("DATA_PATH", "/root/data/"),
        embedding_model=embedding_name,
        embedding_dimensions=embedding_dimensions,
        chunk_size=dm.get("chunk_size", 1000),
        chunk_overlap=dm.get("chunk_overlap", 150),
        distance_metric=dm.get("distance_metric", "cosine"),
        available_pipelines=available_pipelines,
        available_models=available_models,
        available_providers=available_providers,
        auth_enabled=seeded_services.get("chat_app", {}).get("auth", {}).get("enabled", False),
        sources_config=dm.get("sources", {}),
        services_config=seeded_services,
        mcp_servers_config=mcp_servers,
        data_manager_config=dm,
        archi_config=archi_cfg,
        global_config=global_cfg,
    )

    print("[config-seed] static_config upserted")

    # Initialize dynamic config only if empty
    dynamic = cs.get_dynamic_config()
    if dynamic.updated_by is None:
        retrievers = dm.get("retrievers", {})
        hybrid = retrievers.get("hybrid_retriever", {})
        active_model = f"{provider}/{model}" if provider and model else None
        cs.update_dynamic_config(
            active_pipeline=seeded_services.get("chat_app", {}).get("agent_class", "CMSCompOpsAgent"),
            active_model=active_model,
            num_documents_to_retrieve=hybrid.get("num_documents_to_retrieve", 10),
            bm25_weight=hybrid.get("bm25_weight", 0.3),
            semantic_weight=hybrid.get("semantic_weight", 0.7),
            updated_by="seed",
        )
        print("[config-seed] dynamic_config initialized")


def main():
    config_path = os.environ.get("CONFIG_PATH", "/rendered-config/config.yaml")
    seed_entry(config_path, os.environ)


def seed_entry(config_path: str, env: dict):
    print(f"[config-seed] Loading config from {config_path}")
    config = load_config(config_path)
    factory = PostgresServiceFactory.from_env(password_override=env.get("PGPASSWORD") or env.get("PG_PASSWORD"))
    PostgresServiceFactory.set_instance(factory)
    cs = factory.config_service
    seed(config, cs)
    print("Config seeding completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Config seeding failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
