"""CLI for archi.eval: ``python -m archi.eval {list-arms,run}``.

Replaces PR #596's click-based ``archi eval qa`` command group. Changes
from the original: stdlib ``argparse`` (click is not an archi v3
dependency), no workspace/phase subcommands (prepare/run/score were v2
artifact-directory machinery; the v3 engine runs in one pass), and the
tested configuration is an *arm id + config file* instead of
agent-config/agent-spec paths.

Usage::

    python -m archi.eval list-arms
    python -m archi.eval run --arm raw-llm --dataset atoms.yaml \
        --arm-config raw-llm.yaml [--generation <gen id>] \
        [--output report.json] [--format md|json]

``--arm`` may repeat to compare arms in one run; ``--arm-config`` files
match ``--arm`` flags positionally. Config files are JSON or YAML
objects; callable-valued keys (``client``, ``invoke``) accept
``module:attr`` dotted paths so injectable clients work from the CLI.

Deliberately not wired here: an LLM grader (atoms with only
``gold_facts`` report as ``ungraded`` from the CLI until the judge
wiring lands) and live MCP sessions (datasets with live atoms report
``oracle_failed`` unless an invoker-bearing arm setup provides one
programmatically via :func:`archi.eval.engine.run_eval`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from .arms import NotConfiguredError, create_arm, list_arms
from .atoms import DatasetError, load_dataset
from .engine import run_eval
from .report import build_report, render_markdown


class CLIError(Exception):
    """A user-facing CLI failure; the message is printed to stderr."""


def _load_config(path: Optional[str]) -> Dict[str, Any]:
    if path is None:
        return {}
    config_path = Path(path)
    if not config_path.is_file():
        raise CLIError(f"arm config must be an existing file: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    try:
        if config_path.suffix.lower() == ".json":
            payload = json.loads(text)
        else:
            payload = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise CLIError(f"invalid arm config {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CLIError(f"arm config {config_path} must contain an object")
    return payload


def _cmd_list_arms(_args: argparse.Namespace) -> int:
    for entry in list_arms():
        print(f"{entry.arm_id}: {entry.summary}")
        for key, description in sorted(entry.config_keys.items()):
            print(f"    {key}: {description}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    configs: List[Optional[str]] = list(args.arm_config or [])
    if len(configs) > len(args.arm):
        raise CLIError(
            f"got {len(configs)} --arm-config for {len(args.arm)} --arm"
        )
    configs.extend([None] * (len(args.arm) - len(configs)))
    try:
        atoms = load_dataset(args.dataset)
    except DatasetError as exc:
        raise CLIError(str(exc)) from exc
    try:
        arms = [
            create_arm(arm_id, _load_config(config_path))
            for arm_id, config_path in zip(args.arm, configs)
        ]
    except (KeyError, ValueError, NotConfiguredError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        raise CLIError(str(message)) from exc
    try:
        run = run_eval(
            atoms,
            arms,
            generation_id=args.generation,
            dataset=str(args.dataset),
        )
    except NotConfiguredError as exc:
        raise CLIError(str(exc)) from exc
    report = build_report(run)
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(report), end="")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m archi.eval",
        description="Run the archi QA evaluation over registered arms.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser(
        "list-arms", help="list registered arms and their config keys"
    )
    list_parser.set_defaults(func=_cmd_list_arms)

    run_parser = sub.add_parser("run", help="run a dataset against one or more arms")
    run_parser.add_argument(
        "--arm",
        action="append",
        required=True,
        help="registered arm id (repeatable to compare arms)",
    )
    run_parser.add_argument(
        "--dataset", required=True, help="JSON or YAML atom dataset path"
    )
    run_parser.add_argument(
        "--arm-config",
        action="append",
        help="JSON/YAML config file for the matching --arm (positional pairing)",
    )
    run_parser.add_argument(
        "--generation",
        help="explicit OKG generation id pin recorded in the report header",
    )
    run_parser.add_argument("--output", help="write the JSON report to this path")
    run_parser.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="stdout format (default: md)",
    )
    run_parser.set_defaults(func=_cmd_run)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
