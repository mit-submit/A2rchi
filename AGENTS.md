<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

# Working Instructions

Read `openspec/project.md` at the start of every session for project context. Run `openspec list` to see active proposals — this repo typically has many in-flight changes, so always confirm which one you're on before editing.

## Staying on Task

- **Before starting work:** Read the active proposal's `tasks.md`. Identify the specific task you're on. Do not start a second task until the first is verified complete.
- **Don't fix tangential problems.** If you notice something broken that isn't your current task, say "I noticed X — want me to address that after the current task?" Don't silently start working on it.
- **After long tool chains (10+ calls):** Stop and restate what you're doing and why. Check whether you're still on the original task.
- **When you get an error:** Diagnose the root cause before retrying. Don't retry the same thing. Don't pivot to a different approach without explaining why the first one failed.

## Proposals

- Start every proposal with root cause analysis: "Why is this broken / why is this needed now?"
- Every task in `tasks.md` must end with a verification step that checks real output (a passing run, a populated result JSON, an end-to-end deployment check) — not just "code compiles" or "imports resolve."
- Run `openspec validate <change-id> --strict --no-interactive` before requesting approval.

## Verification

- Never mark a benchmark/eval task complete without running it and inspecting the actual output files (e.g., confirm `token_usage`, `model_used`, `trace_events` are populated in result JSON).
- If something "should work" but the numbers don't change, there's a silent failure — investigate before moving on.
- For deployment/service changes, validate both streamed events and persisted DB rows (e.g., `agent_traces.events`) when debugging trace or tool-call mismatches. Match the real runtime path: verify which code path the running service imports (workspace source vs installed `site-packages`) and patch the active path.

# Repository Guidelines

## Project Structure & Module Organization
- `src/` holds core (`src/archi`), CLI (`src/cli`), ingestion (`src/data_manager`), interfaces (`src/interfaces`), and utilities (`src/utils`).
- `tests/` includes `smoke/` and `pr_preview_config/`.
- `docs/` contains the mkdocs site; `requirements/` and `src/cli/templates/dockerfiles/` store base image requirements; `examples/` has sample configs.

## Codebase Map
- CLI entrypoint is `src/cli/cli_main.py`, with registries in `src/cli/service_registry.py` and `src/cli/source_registry.py`, and managers in `src/cli/managers/`.
- Service entrypoints live in `src/bin/` and wire Flask apps from `src/interfaces/`.
- Runtime config is loaded from `/root/archi/configs/` by `src/utils/config_loader.py`; CLI deployments render under `~/.archi/archi-<name>` (override with `Archi_DIR`).
- Core orchestration lives in `src/archi/archi.py` with pipelines in `src/archi/pipelines/`; ingestion is in `src/data_manager/`.

## Build, Test, and Development Commands
- `pip install -e .` installs the package in editable mode for local development.
- `archi --help` verifies the CLI entrypoint defined in `pyproject.toml`.
- `cd docs && mkdocs serve` previews documentation locally.

## Coding Style & Naming Conventions
- Python 3.7+; follow PEP 8 with 4-space indentation.
- Use `snake_case` for modules/functions and `PascalCase` for classes; keep filenames descriptive (e.g., `test_interfaces.py`).
- Import ordering is generally maintained with `isort` when formatting is applied.
- Shell scripts under `scripts/` and `tests/smoke/` use `bash` with `set -euo pipefail`.

## Testing Guidelines
- **Unit tests:** Run `pytest tests/unit/ -v --tb=short` (requires project dependencies: `pip install ".[all]"`).
- **UI tests:** Run `npx playwright test` against a running deployment (set `BASE_URL` env var). Install with `npm ci && npx playwright install --with-deps chromium`.
- **Smoke tests:** Run via `scripts/dev/run_smoke_preview.sh <name>`. Requires Ollama with a model pulled, Docker, and the archi CLI.
- **Lint:** Run `black --check .` and `isort --check .` for formatting checks.
- **CI:** All PR checks run on `ubuntu-latest` GitHub runners. PR CI includes lint, unit tests, smoke deployment, and Playwright UI tests.

## Commit & Pull Request Guidelines
- Recent history uses short, lowercase summaries (e.g., `fix bug`, `split data manager...`); keep commits concise and descriptive.
- PRs should include: a brief summary, test results, and documentation impact; link related issues and include screenshots/logs when UI or API changes are involved.

## Agent Workflow
- When changing user-facing behavior, CLI flags, configuration, or public APIs, update the relevant docs in `docs/` and/or `README.md` in the same change.
- If no docs change is needed, note the reason briefly in the PR description or commit message.

## Deployment & Validation Policy
- **Match the real runtime path before debugging:** Verify which code path the running service imports (workspace source vs installed `site-packages`) and patch/reload the active path.
- **Deployment assumptions must be explicit:** State which container/service is being validated (for example `chatbot-debug` and its dependent `postgres-debug` / `data-manager-debug`).
- **Always validate behavior after changes:** Do not stop at code edits. Run at least one end-to-end check against the running deployment and confirm expected outputs in logs/trace/events.
- **Use source-of-truth checks for trace bugs:** Validate both streamed events and persisted DB trace rows (for example `agent_traces.events`) when debugging tool-call rendering mismatches.
- **Iterate until intent is confirmed:** If validation fails or is inconclusive, continue debugging and re-test after each fix until the observed behavior matches the requested goal.
