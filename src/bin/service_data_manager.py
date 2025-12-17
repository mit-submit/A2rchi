#!/bin/python
import os
import threading
from typing import Callable, Dict

from flask import Flask

from src.data_manager.data_manager import DataManager
from src.data_manager.scheduler import CronScheduler
from src.interfaces.uploader_app.app import FlaskAppWrapper
from src.utils.config_loader import load_config
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)


def main() -> None:
    # set API keys in env for downstream clients
    os.environ["ANTHROPIC_API_KEY"] = read_secret("ANTHROPIC_API_KEY")
    os.environ["OPENAI_API_KEY"] = read_secret("OPENAI_API_KEY")
    os.environ["HUGGING_FACE_HUB_TOKEN"] = read_secret("HUGGING_FACE_HUB_TOKEN")

    config = load_config()
    services_config = config["services"]
    data_manager_cfg = services_config.get("data_manager", {})

    data_manager = DataManager(run_ingestion=True)
    lock = threading.Lock()

    def run_locked(name: str, func: Callable[[], None]) -> None:
        with lock:
            logger.info("Running ingestion task: %s", name)
            func()

    def trigger_update() -> None:
        with lock:
            data_manager.update_vectorstore(force=True)

    schedule_map: Dict[str, Callable[[], None]] = {
        "links": data_manager.collect_links,
        "git": data_manager.collect_git,
        "sso": data_manager.collect_sso,
        "jira": data_manager.collect_jira,
        "redmine": data_manager.collect_redmine,
    }

    scheduler = CronScheduler()
    sources_cfg = config.get("data_manager", {}).get("sources", {}) or {}
    for source_name, source_cfg in sources_cfg.items():
        if source_name not in schedule_map:
            continue
        schedule = (source_cfg or {}).get("schedule")
        if not schedule:
            continue
        scheduler.add_job(
            name=source_name,
            cron=schedule,
            callback=lambda name=source_name: run_locked(name, schedule_map[name]),
        )

    if scheduler.jobs:
        scheduler.start()

    app = Flask(
        __name__,
        template_folder=data_manager_cfg.get("template_folder"),
        static_folder=data_manager_cfg.get("static_folder"),
    )
    uploader = FlaskAppWrapper(app, post_update_hook=trigger_update)
    uploader.run(
        debug=data_manager_cfg.get("flask_debug_mode", False),
        port=data_manager_cfg.get("port", 7871),
        host=data_manager_cfg.get("host", "0.0.0.0"),
    )


if __name__ == "__main__":
    main()
