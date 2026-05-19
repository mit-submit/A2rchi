"""Probe where ChatOllama 1.0.1 surfaces thinking content.

Designed to run inside the A2rchi benchmarking container which has
langchain_ollama installed. Calls ChatOllama with reasoning=True and
reasoning=False against gemma4:26b on the host's Ollama.
"""

from __future__ import annotations

import json
import os

os.environ["OLLAMA_HOST"] = "http://localhost:11434"

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama


def dump_response(label: str, resp) -> None:
    print(f"=== {label} ===")
    print(f"  content (first 300): {resp.content[:300]!r}")
    print(f"  additional_kwargs keys: {list(resp.additional_kwargs.keys())}")
    for k, v in resp.additional_kwargs.items():
        if isinstance(v, str):
            print(f"    {k} (first 300): {v[:300]!r}")
        else:
            print(f"    {k}: {type(v).__name__} = {str(v)[:200]}")
    print(f"  response_metadata keys: {list(resp.response_metadata.keys())}")
    for k, v in resp.response_metadata.items():
        if isinstance(v, str):
            print(f"    {k}: {v[:120]!r}")
        elif isinstance(v, dict):
            print(f"    {k}: dict with keys {sorted(v.keys())[:10]}")
        else:
            print(f"    {k}: {type(v).__name__} = {str(v)[:100]}")
    print(f"  usage_metadata: {resp.usage_metadata}")
    print()


def main() -> None:
    base_url = "http://localhost:11434"
    prompt = "What is 2 + 2? Reply with one short sentence."

    chat_on = ChatOllama(
        model="gemma4:26b",
        base_url=base_url,
        reasoning=True,
        num_predict=400,
        temperature=0.0,
    )
    r1 = chat_on.invoke([HumanMessage(content=prompt)])
    dump_response("reasoning=True", r1)

    chat_off = ChatOllama(
        model="gemma4:26b",
        base_url=base_url,
        reasoning=False,
        num_predict=400,
        temperature=0.0,
    )
    r2 = chat_off.invoke([HumanMessage(content=prompt)])
    dump_response("reasoning=False", r2)

    chat_default = ChatOllama(
        model="gemma4:26b",
        base_url=base_url,
        num_predict=400,
        temperature=0.0,
    )
    r3 = chat_default.invoke([HumanMessage(content=prompt)])
    dump_response("reasoning=(unset/default)", r3)


if __name__ == "__main__":
    main()
