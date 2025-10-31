import time
from typing import List, Optional

import numpy as np

from src.a2rchi.models.base import BaseCustomLLM
from src.utils.logging import get_logger

logger = get_logger(__name__)


class DumbLLM(BaseCustomLLM):
    """
    A simple Dumb LLM, perfect for testing.
    """

    filler: str = None
    sleep_time_mean: int = 3

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
    ) -> str:
        sleep_time = np.random.normal(self.sleep_time_mean, 1)
        logger.info(f"DumbLLM: sleeping {sleep_time}")
        time.sleep(sleep_time)
        return "I am just a dumb LLM, I will give you a number: " + str(np.random.randint(10000, 99999))
