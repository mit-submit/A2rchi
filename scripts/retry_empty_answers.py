#!/usr/bin/env python3
"""Retry empty-answer questions from a benchmark results JSON.

Usage (inside the benchmark container or with the right env):
    python scripts/retry_empty_answers.py <results.json> <config_index> [--timeout 600] [--output patched.json]

This script:
1. Loads a completed benchmark results JSON
2. Identifies questions with empty answers in the specified config
3. Re-runs just those questions using the same pipeline/model
4. Patches the results back into the JSON and writes the output
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.archi.archi import archi
from src.archi.pipelines.agents.agent_spec import load_agent_spec
from src.utils.config_access import get_full_config
from src.utils.logging import get_logger, setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory

setup_logging()
logger = get_logger(__name__)

# Initialize postgres factory (same as service_benchmark.py)
factory = PostgresServiceFactory.from_env(password_override=os.environ.get("PG_PASSWORD"))
PostgresServiceFactory.set_instance(factory)


def find_empty_questions(config_results: dict) -> list:
    """Return list of (question_key, question_dict) for empty answers."""
    qr = config_results.get("single_question_results", {})
    empty = []
    for key in sorted(qr.keys(), key=lambda k: int(k.split("_")[1])):
        if not qr[key].get("answer", "").strip():
            empty.append((key, qr[key]))
    return empty


def prepare_messages(raw_messages):
    """Format langchain Messages for storage (mirrors service_benchmark logic)."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    tool_results = {}
    for msg in raw_messages:
        if type(msg) is ToolMessage:
            tcid = getattr(msg, "tool_call_id", None)
            if tcid:
                tool_results[tcid] = getattr(msg, "content", "")

    formatted = []
    for msg in raw_messages:
        if type(msg) is AIMessage:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    entry = {
                        "type": "tool_call",
                        "tool_name": tc.get("name"),
                        "tool_args": tc.get("args", {}),
                        "total_duration": getattr(msg, "response_metadata", {}).get("total_duration"),
                    }
                    tcid = tc.get("id")
                    if tcid and tcid in tool_results:
                        entry["tool_output"] = tool_results[tcid]
                    formatted.append(entry)
            elif hasattr(msg, "content"):
                content = msg.content or ""
                thinking = ""
                think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL)
                think_matches = think_pattern.findall(content)
                if think_matches:
                    thinking = "\n".join(think_matches)
                    content = think_pattern.sub("", content).strip()
                additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
                reasoning = additional_kwargs.get("reasoning_content", "")
                if reasoning and not thinking:
                    thinking = str(reasoning)
                entry = {
                    "type": "ai_message",
                    "content": content,
                    "total_duration": getattr(msg, "response_metadata", {}).get("total_duration"),
                }
                if thinking:
                    entry["thinking"] = thinking
                formatted.append(entry)
    return formatted


def main():
    parser = argparse.ArgumentParser(description="Retry empty-answer benchmark questions")
    parser.add_argument("results_json", help="Path to the benchmark results JSON file")
    parser.add_argument("config_index", type=int, help="0-based index of the config to retry")
    parser.add_argument("--timeout", type=float, default=600.0, help="Copilot timeout in seconds (default: 600)")
    parser.add_argument("--output", help="Output path for patched JSON (default: overwrite input)")
    parser.add_argument("--config-yaml", help="Path to the config YAML for this benchmark config")
    parser.add_argument("--dry-run", action="store_true", help="Just list empty questions without retrying")
    args = parser.parse_args()

    # Load results
    logger.info("Loading results from %s", args.results_json)
    with open(args.results_json) as f:
        data = json.load(f)

    results_list = data["benchmarking_results"]
    if args.config_index >= len(results_list):
        logger.error("Config index %d out of range (have %d configs)", args.config_index, len(results_list))
        sys.exit(1)

    config_results = results_list[args.config_index]
    config_name = config_results.get("configuration", {}).get("services", {}).get("benchmarking", {}).get("name", "unknown")
    logger.info("Target config [%d]: %s", args.config_index, config_name)

    # Find empty answers
    empty_questions = find_empty_questions(config_results)
    logger.info("Found %d empty-answer questions", len(empty_questions))

    if not empty_questions:
        logger.info("Nothing to retry!")
        return

    for key, qdata in empty_questions:
        logger.info("  %s (%.1fs): %s", key, qdata.get("time_elapsed", 0), qdata.get("question", "")[:80])

    if args.dry_run:
        logger.info("Dry run — not retrying")
        return

    # Load config for the pipeline
    if args.config_yaml:
        logger.info("Loading config from %s", args.config_yaml)
        import yaml
        with open(args.config_yaml) as f:
            config = yaml.safe_load(f)
        # Write to the standard config path so get_full_config() picks it up
        config_path = "/root/archi/config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, stream=f)
        # Seed to postgres
        try:
            from src.cli.tools.config_seed import seed as config_seed
            config_seed(config, factory.config_service)
            logger.info("Seeded config to postgres")
        except Exception:
            logger.warning("Failed to seed config to postgres", exc_info=True)

    config = get_full_config()

    # Inject copilot_timeout into config
    bench_cfg = config.get("services", {}).get("benchmarking", {})
    bench_cfg["copilot_timeout"] = args.timeout
    logger.info("Set copilot_timeout to %.0fs", args.timeout)

    # Extract pipeline settings from stored config
    stored_cfg = config_results.get("configuration", {}).get("services", {}).get("benchmarking", {})
    pipeline_name = stored_cfg.get("agent_class") or bench_cfg.get("agent_class")
    provider = stored_cfg.get("provider") or bench_cfg.get("provider")
    model = stored_cfg.get("model") or bench_cfg.get("model")
    agent_md_file = stored_cfg.get("agent_md_file") or bench_cfg.get("agent_md_file")
    ollama_url = stored_cfg.get("ollama_url") or bench_cfg.get("ollama_url")

    if ollama_url:
        os.environ["OLLAMA_HOST"] = str(ollama_url)

    logger.info("Pipeline: %s, Provider: %s, Model: %s", pipeline_name, provider, model)

    # Load agent spec
    agent_spec = load_agent_spec(Path(str(agent_md_file)))

    # Create the chain
    chain = archi(
        pipeline_name,
        agent_spec=agent_spec,
        default_provider=provider,
        default_model=model,
        prompt_overrides={},
    )

    # Retry each empty question
    succeeded = 0
    failed = 0
    for key, qdata in empty_questions:
        question = qdata["question"]
        logger.info("Retrying %s: %s", key, question[:80])

        formatted_question = [("User", question)]
        start = time.perf_counter()
        try:
            result = chain(history=formatted_question)
            elapsed = time.perf_counter() - start
        except Exception as exc:
            elapsed = time.perf_counter() - start
            logger.error("Failed %s after %.1fs: %s", key, elapsed, exc)
            failed += 1
            continue

        answer = result.get("answer", "")
        if answer.strip():
            logger.info("  SUCCESS (%.1fs, %d chars): %s", elapsed, len(answer), answer[:100])
            succeeded += 1
        else:
            logger.warning("  STILL EMPTY after %.1fs", elapsed)
            failed += 1

        # Patch the result back into the data
        qr = config_results["single_question_results"][key]
        qr["time_elapsed"] = elapsed
        qr["answer"] = answer
        qr["messages"] = prepare_messages(result.get("messages", []))
        qr["retry"] = True  # mark as retried

        # Update sources
        sources_metadata = []
        sources_trunc_content = []
        for doc in result.get("source_documents", []):
            metadata = getattr(doc, "metadata", {}) or {}
            sources_metadata.append(metadata)
            sources_trunc_content.append(getattr(doc, "page_content", "")[:300])
        qr["sources_metadata"] = sources_metadata
        qr["sources_trunc_content"] = sources_trunc_content

    logger.info("Retry complete: %d succeeded, %d failed out of %d", succeeded, failed, len(empty_questions))

    # Write patched results
    output_path = args.output or args.results_json
    logger.info("Writing patched results to %s", output_path)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info("Done!")


if __name__ == "__main__":
    main()
