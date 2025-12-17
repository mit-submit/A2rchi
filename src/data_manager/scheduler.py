from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from croniter import croniter

from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CronJob:
    name: str
    cron: str
    callback: Callable[[], None]
    next_run: datetime = field(default_factory=datetime.now)

    def schedule_next(self, base_time: datetime) -> None:
        self.next_run = croniter(self.cron, base_time).get_next(datetime)


class CronScheduler:
    """Simple cron scheduler that runs jobs in a background thread."""

    def __init__(self, poll_interval: float = 1.0) -> None:
        self.poll_interval = poll_interval
        self.jobs: List[CronJob] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add_job(self, name: str, cron: str, callback: Callable[[], None]) -> None:
        job = CronJob(name=name, cron=cron, callback=callback)
        job.schedule_next(datetime.now())
        self.jobs.append(job)
        logger.info("Scheduled %s with cron '%s' (next run %s)", name, cron, job.next_run)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            next_wake = None

            for job in self.jobs:
                if job.next_run <= now:
                    logger.info("Running scheduled job %s", job.name)
                    try:
                        job.callback()
                    except Exception as exc:
                        logger.warning("Scheduled job %s failed: %s", job.name, exc)
                    job.schedule_next(now)

                if next_wake is None or job.next_run < next_wake:
                    next_wake = job.next_run

            if next_wake:
                sleep_for = max(0.0, (next_wake - datetime.now()).total_seconds())
                time.sleep(min(self.poll_interval, sleep_for))
            else:
                time.sleep(self.poll_interval)
