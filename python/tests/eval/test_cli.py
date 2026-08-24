"""archi.eval.cli — list-arms and run smoke coverage, offline.

The run path resolves its answering client through the CLI's
``module:attr`` dotted-path form, pointed at a scripted module written
into tmp_path — so the real injection mechanism is exercised with no
network and no provider SDK.
"""
import json

import pytest

from archi.eval.cli import main

CLIENT_MODULE = '''
"""Scripted answering client for the archi.eval CLI tests."""
LOOKUP = {
    "Which CMSSW 14_0 release is the latest announced?": "CMSSW_14_0_7",
    "What storage element serves T2_US_MIT?": "se01.cmsaf.mit.edu",
}


def answer(question, *, model, system_prompt=None):
    return {
        "answer": LOOKUP.get(question, "no answer"),
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "cost_usd": 0.0002,
        "generation_id": "gen:cli",
    }
'''


@pytest.fixture
def client_path(tmp_path, monkeypatch):
    """Write the scripted client module and make it importable."""
    (tmp_path / "scripted_client.py").write_text(CLIENT_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return "scripted_client:answer"


def _write_config(tmp_path, payload, name="arm.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_list_arms_prints_ids_and_config_keys(capsys):
    assert main(["list-arms"]) == 0
    out = capsys.readouterr().out
    for arm_id in ("raw-llm", "okg-mcp", "openwebui-chat", "codex"):
        assert f"{arm_id}:" in out
    assert "deployment:" in out
    assert "client:" in out


def test_run_with_injected_client_renders_markdown(
    tmp_path, capsys, smoke_dataset, client_path
):
    config = _write_config(tmp_path, {"model": "test-model", "client": client_path})
    exit_code = main(
        [
            "run",
            "--arm",
            "raw-llm",
            "--dataset",
            str(smoke_dataset),
            "--arm-config",
            config,
            "--generation",
            "gen:cli",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Archi QA Evaluation Report" in out
    assert "## Arm `raw-llm`" in out
    assert "Pinned generation: `gen:cli`" in out
    # The two questions the scripted client knows pass; the graded-only
    # and live atoms report ungraded / oracle_failed from the CLI, which
    # wires neither a judge nor an MCP session.
    assert "`cmssw-latest-14x`: scored [pass]" in out
    assert "`xrootd-fallback`: ungraded" in out
    assert "`live-open-downtimes`: oracle_failed" in out


def test_run_json_format_and_output_file(
    tmp_path, capsys, smoke_dataset, client_path
):
    config = _write_config(tmp_path, {"model": "test-model", "client": client_path})
    output = tmp_path / "report.json"
    assert (
        main(
            [
                "run",
                "--arm",
                "raw-llm",
                "--dataset",
                str(smoke_dataset),
                "--arm-config",
                config,
                "--format",
                "json",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert printed == written
    (arm,) = written["arms"]
    assert arm["arm"] == "raw-llm"
    # Six answered atoms: the live one fails its oracle before the arm
    # is ever asked, so it contributes no tokens.
    assert arm["tokens"]["prompt"] == 11 * 6
    assert written["generation_id"] == "gen:cli"


def test_run_two_arms_pairs_configs_positionally(
    tmp_path, capsys, smoke_dataset, client_path
):
    raw_config = _write_config(
        tmp_path, {"model": "test-model", "client": client_path}
    )
    okg_config = _write_config(
        tmp_path, {"deployment": "cern-team"}, name="okg.json"
    )
    assert (
        main(
            [
                "run",
                "--arm",
                "raw-llm",
                "--arm",
                "okg-mcp",
                "--dataset",
                str(smoke_dataset),
                "--arm-config",
                raw_config,
                "--arm-config",
                okg_config,
            ]
        )
        == 1
    )
    # raw-llm resolved fine; okg-mcp has no live wiring, and that is a
    # setup failure the run refuses to score as zeros.
    assert "no live MCP wiring" in capsys.readouterr().err


def test_run_reports_dataset_errors(tmp_path, capsys, client_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: q1\n", encoding="utf-8")
    config = _write_config(tmp_path, {"model": "m", "client": client_path})
    assert (
        main(
            [
                "run",
                "--arm",
                "raw-llm",
                "--dataset",
                str(bad),
                "--arm-config",
                config,
            ]
        )
        == 1
    )
    assert "error:" in capsys.readouterr().err


def test_run_reports_unknown_arm(capsys, smoke_dataset):
    assert main(["run", "--arm", "nope", "--dataset", str(smoke_dataset)]) == 1
    assert "unknown arm 'nope'" in capsys.readouterr().err


def test_run_reports_not_configured_stub(tmp_path, capsys, smoke_dataset):
    config = _write_config(tmp_path, {"workdir": str(tmp_path)})
    assert (
        main(
            [
                "run",
                "--arm",
                "codex",
                "--dataset",
                str(smoke_dataset),
                "--arm-config",
                config,
            ]
        )
        == 1
    )
    assert "registered stub" in capsys.readouterr().err


def test_run_rejects_extra_arm_configs(tmp_path, capsys, smoke_dataset):
    config = _write_config(tmp_path, {"model": "m", "client": "x:y"})
    assert (
        main(
            [
                "run",
                "--arm",
                "raw-llm",
                "--dataset",
                str(smoke_dataset),
                "--arm-config",
                config,
                "--arm-config",
                config,
            ]
        )
        == 1
    )
    assert "2 --arm-config for 1 --arm" in capsys.readouterr().err


def test_run_reports_missing_config_file(capsys, smoke_dataset, tmp_path):
    assert (
        main(
            [
                "run",
                "--arm",
                "raw-llm",
                "--dataset",
                str(smoke_dataset),
                "--arm-config",
                str(tmp_path / "absent.json"),
            ]
        )
        == 1
    )
    assert "existing file" in capsys.readouterr().err


def test_module_entry_point_is_wired():
    import archi.eval.__main__ as entry

    assert entry.main is main


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        main([])
