from typing import List, Optional

import requests

from src.a2rchi.models.base import BaseCustomLLM
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ClaudeLLM(BaseCustomLLM):
    """
    An LLM class that uses Anthropic's Claude model.
    """

    api_key: str = "INSERT KEY HERE!!!"
    base_url: str = "https://api.anthropic.com/v1/messages"
    model_name: str = "claude-3-5-sonnet-20240620"

    verbose: bool = False

    def _call(
        self,
        prompt: str = None,
        stop: Optional[List[str]] = None,
        max_tokens: int = 1024,
    ) -> str:
        if stop is not None:
            logger.warning("Currently this model does not support stop tokens")

        if self.verbose:
            logger.info(f"Starting call to Claude with prompt: {prompt}")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }

        if self.verbose:
            logger.info("Sending request to Claude API")

        response = requests.post(self.base_url, headers=headers, json=payload)

        if response.status_code == 200:
            completion = response.json()["content"][0]["text"]
            if self.verbose:
                logger.info(f"Received response from Claude API: {completion}")
            return completion
        else:
            raise Exception(
                f"API request to Claude failed with status {response.status_code}, {response.text}"
            )
