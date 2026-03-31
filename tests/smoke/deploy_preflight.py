#!/usr/bin/env python3
"""Deployment preflight checks for the A2rchi container environment.

Validates that critical configuration and services are properly set up
before running smoke tests.  Run inside the container after deployment.

Usage:
    python3 tests/smoke/deploy_preflight.py

Environment:
    DM_BASE_URL — data-manager base URL (default: http://localhost:7871)
    OLLAMA_URL  — Ollama API URL (default: http://localhost:11434)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = "\033[92mPASS\033[0m"
_FAIL = "\033[91mFAIL\033[0m"
_WARN = "\033[93mWARN\033[0m"

_failures = []
_warnings = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [{_PASS}] {name}")
    else:
        _failures.append(name)
        detail_str = f" — {detail}" if detail else ""
        print(f"  [{_FAIL}] {name}{detail_str}")


def warn(name, condition, detail=""):
    if condition:
        print(f"  [{_PASS}] {name}")
    else:
        _warnings.append(name)
        detail_str = f" — {detail}" if detail else ""
        print(f"  [{_WARN}] {name}{detail_str}")


# ---------------------------------------------------------------------------
# 1. Required environment variables
# ---------------------------------------------------------------------------


def check_env_vars():
    print("\n[deploy-preflight] Checking environment variables...")

    # Critical — deployment fails without these
    critical_vars = [
        "PG_PASSWORD",
        "OLLAMA_URL",
    ]
    for var in critical_vars:
        value = os.environ.get(var, "")
        check(f"env:{var} is set", bool(value.strip()), f"'{var}' is empty or missing")

    # Important for tool functionality
    dm_api_token = os.environ.get("DM_API_TOKEN", "")
    warn(
        "env:DM_API_TOKEN is set",
        bool(dm_api_token.strip()),
        "Tools that query data-manager will fail with auth redirects",
    )

    # Check GITHUB_TOKEN if not using BYOK-only mode
    github_token = os.environ.get("GITHUB_TOKEN", "")
    warn(
        "env:GITHUB_TOKEN is set (or BYOK-only)",
        bool(github_token.strip()),
        "SDK auth may fail if not using BYOK-only mode",
    )


# ---------------------------------------------------------------------------
# 2. Catalog API reachable and authenticated
# ---------------------------------------------------------------------------


def check_catalog_api():
    print("\n[deploy-preflight] Checking catalog API...")
    dm_base = os.environ.get("DM_BASE_URL", "http://localhost:7871")
    dm_token = os.environ.get("DM_API_TOKEN", "")

    try:
        import requests
    except ImportError:
        warn("requests installed", False, "cannot test catalog API")
        return

    # Health check
    try:
        headers = {}
        if dm_token:
            headers["Authorization"] = f"Bearer {dm_token}"
        resp = requests.get(
            f"{dm_base}/api/health",
            headers=headers,
            timeout=5,
            allow_redirects=False,
        )
        check(
            "catalog health returns 200 (not redirect)",
            resp.status_code == 200,
            f"got {resp.status_code}"
            + (
                f" → {resp.headers.get('Location', '?')}"
                if resp.status_code in (301, 302, 303, 307, 308)
                else ""
            ),
        )
    except requests.ConnectionError:
        check("catalog API reachable", False, f"connection refused at {dm_base}")
    except requests.Timeout:
        check("catalog API reachable", False, f"timeout at {dm_base}")
    except Exception as exc:
        check("catalog API reachable", False, str(exc))


# ---------------------------------------------------------------------------
# 3. Ollama model available
# ---------------------------------------------------------------------------


def check_ollama():
    print("\n[deploy-preflight] Checking Ollama...")
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    expected_model = os.environ.get("OLLAMA_MODEL", "")

    try:
        import requests
    except ImportError:
        warn("requests installed", False, "cannot test Ollama")
        return

    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        check("Ollama reachable", resp.status_code == 200, f"status={resp.status_code}")

        if resp.status_code == 200 and expected_model:
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            # Match either exact name or name without tag
            found = any(
                expected_model == m or expected_model == m.split(":")[0] for m in models
            )
            check(
                f"Ollama model '{expected_model}' available",
                found,
                f"available models: {', '.join(models[:5])}",
            )
    except requests.ConnectionError:
        check("Ollama reachable", False, f"connection refused at {ollama_url}")
    except requests.Timeout:
        check("Ollama reachable", False, f"timeout at {ollama_url}")
    except Exception as exc:
        check("Ollama reachable", False, str(exc))


# ---------------------------------------------------------------------------
# 4. Vectorstore non-empty
# ---------------------------------------------------------------------------


def check_vectorstore():
    print("\n[deploy-preflight] Checking vectorstore...")
    pg_host = os.environ.get("PGHOST", os.environ.get("PG_HOST", "localhost"))
    pg_port = os.environ.get("PGPORT", os.environ.get("PG_PORT", "5432"))
    pg_user = os.environ.get("PGUSER", os.environ.get("PG_USER", "archi"))
    pg_pass = os.environ.get("PGPASSWORD", os.environ.get("PG_PASSWORD", ""))
    pg_db = os.environ.get("PGDATABASE", os.environ.get("PG_DATABASE", "archi"))

    try:
        import psycopg2
    except ImportError:
        warn("psycopg2 installed", False, "cannot check vectorstore")
        return

    try:
        conn = psycopg2.connect(
            host=pg_host,
            port=pg_port,
            user=pg_user,
            password=pg_pass,
            dbname=pg_db,
            connect_timeout=5,
        )
        cur = conn.cursor()
        # Check if the embeddings table exists and has rows
        cur.execute("""
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_name = 'langchain_pg_embedding'
        """)
        table_exists = cur.fetchone()[0] > 0

        if table_exists:
            cur.execute("SELECT count(*) FROM langchain_pg_embedding")
            row_count = cur.fetchone()[0]
            check(
                f"vectorstore has embeddings ({row_count} rows)",
                row_count > 0,
                "vectorstore is empty — ingestion may have failed",
            )
        else:
            warn(
                "vectorstore table exists",
                False,
                "langchain_pg_embedding table not found",
            )

        cur.close()
        conn.close()
    except psycopg2.OperationalError as exc:
        check("vectorstore DB reachable", False, str(exc).strip().split("\n")[0])
    except Exception as exc:
        check("vectorstore check", False, str(exc))


# ---------------------------------------------------------------------------
# 5. Code version sentinel
# ---------------------------------------------------------------------------


def check_code_version():
    print("\n[deploy-preflight] Checking code version...")
    # Verify the adapter module has poll_timeout parameter (post-fix sentinel)
    try:
        import inspect

        from src.archi.pipelines.copilot_agents.copilot_event_adapter import CopilotEventAdapter

        sig = inspect.signature(CopilotEventAdapter.iter_outputs)
        has_poll_timeout = "poll_timeout" in sig.parameters
        check(
            "CopilotEventAdapter.iter_outputs has poll_timeout param",
            has_poll_timeout,
            "stale code — missing poll_timeout fix",
        )
    except ImportError:
        warn("copilot_event_adapter importable", False, "module not found")
    except Exception as exc:
        warn("code version check", False, str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("[deploy-preflight] Running deployment validation checks")
    print("=" * 60)

    check_env_vars()
    check_catalog_api()
    check_ollama()
    check_vectorstore()
    check_code_version()

    print()
    if _failures:
        print(f"[deploy-preflight] {len(_failures)} FAILED check(s):")
        for f in _failures:
            print(f"  - {f}")
    if _warnings:
        print(f"[deploy-preflight] {len(_warnings)} WARNING(s):")
        for w in _warnings:
            print(f"  - {w}")

    if _failures:
        print(
            f"\n[deploy-preflight] FAILED — {len(_failures)} critical check(s) did not pass"
        )
        sys.exit(1)
    else:
        print("\n[deploy-preflight] PASSED — all critical checks OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
