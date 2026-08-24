"""archi.eval.arms — registry resolution + NotConfigured behavior."""
import pytest

from archi.eval.arms import (
    AnswerRecord,
    ArmContext,
    NotConfiguredError,
    create_arm,
    list_arms,
)
from archi.eval.atoms import validate_atom

ATOM = validate_atom(
    {"id": "q1", "question": "What?", "checks": [{"kind": "exact", "value": "42"}]}
)
CTX = ArmContext(run_id="run-test")


def test_registry_lists_the_four_shipped_arms():
    entries = {entry.arm_id: entry for entry in list_arms()}
    assert set(entries) >= {"raw-llm", "okg-mcp", "openwebui-chat", "codex"}
    assert "deployment" in entries["okg-mcp"].config_keys
    assert "client" in entries["raw-llm"].config_keys
    for entry in entries.values():
        assert entry.summary


def test_unknown_arm_names_the_known_ones():
    with pytest.raises(KeyError, match="unknown arm 'nope'.*raw-llm"):
        create_arm("nope")


def test_unknown_config_key_rejected():
    with pytest.raises(ValueError, match="unknown config key.*bogus"):
        create_arm("raw-llm", {"bogus": 1})


def test_raw_llm_requires_model_and_client():
    with pytest.raises(NotConfiguredError, match="requires config key 'model'"):
        create_arm("raw-llm", {"client": lambda *a, **k: "x"})
    with pytest.raises(NotConfiguredError, match="requires config key 'client'"):
        create_arm("raw-llm", {"model": "m"})


def test_raw_llm_with_injected_callable():
    seen = {}

    def client(question, *, model, system_prompt):
        seen.update(question=question, model=model, system_prompt=system_prompt)
        return {
            "answer": "42",
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "cost_usd": 0.001,
            "vendor_id": "r-1",
        }

    arm = create_arm(
        "raw-llm", {"model": "test-model", "client": client, "system_prompt": "sp"}
    )
    record = arm.answer(ATOM, CTX)
    assert seen == {"question": "What?", "model": "test-model", "system_prompt": "sp"}
    assert record.answer == "42"
    assert record.prompt_tokens == 7 and record.completion_tokens == 3
    assert record.cost_usd == 0.001
    assert record.extra == {"vendor_id": "r-1"}
    assert record.latency_ms is not None and record.latency_ms >= 0
    assert "test-model" in arm.describe()


def test_raw_llm_client_string_answer():
    arm = create_arm(
        "raw-llm", {"model": "m", "client": lambda q, **kw: "plain answer"}
    )
    record = arm.answer(ATOM, CTX)
    assert record.answer == "plain answer"


def test_raw_llm_bad_dotted_path():
    with pytest.raises(NotConfiguredError, match="could not import"):
        create_arm("raw-llm", {"model": "m", "client": "no.such.module:client"})
    with pytest.raises(NotConfiguredError, match="callable or a 'module:attr'"):
        create_arm("raw-llm", {"model": "m", "client": "not-a-dotted-path"})


def test_okg_mcp_without_invoke_raises_not_configured():
    arm = create_arm("okg-mcp", {"deployment": "cern-team"})
    assert "cern-team" in arm.describe()
    with pytest.raises(NotConfiguredError, match="no live MCP wiring"):
        arm.answer(ATOM, CTX)


def test_okg_mcp_adapter_seam():
    calls = []

    def invoke(tool, arguments):
        calls.append((tool, arguments))
        return {"answer": "42", "generation_id": "gen:abc"}

    arm = create_arm(
        "okg-mcp",
        {"deployment": "cern-team", "ask_tool": "archi_ask", "invoke": invoke},
    )
    record = arm.answer(ATOM, CTX)
    assert calls == [("archi_ask", {"question": "What?"})]
    assert record.answer == "42"
    assert record.generation_id == "gen:abc"


def test_okg_mcp_requires_deployment():
    with pytest.raises(NotConfiguredError, match="requires config key 'deployment'"):
        create_arm("okg-mcp", {})


@pytest.mark.parametrize(
    "arm_id, config",
    [
        (
            "openwebui-chat",
            {
                "base_url": "https://chat.example.org",
                "api_key_env": "OPENWEBUI_API_KEY",
                "model": "archi",
            },
        ),
        ("codex", {"workdir": "."}),
    ],
)
def test_stub_arms_construct_then_raise_not_configured(arm_id, config):
    arm = create_arm(arm_id, config)
    assert arm.describe()
    with pytest.raises(NotConfiguredError, match="registered stub"):
        arm.answer(ATOM, CTX)


def test_stub_arms_require_their_config():
    with pytest.raises(NotConfiguredError, match="requires config key 'base_url'"):
        create_arm("openwebui-chat", {"api_key_env": "K", "model": "m"})
    with pytest.raises(NotConfiguredError, match="requires config key 'workdir'"):
        create_arm("codex", {})


def test_answer_record_requires_answer_xor_error():
    with pytest.raises(ValueError, match="exactly one of answer or error"):
        AnswerRecord(atom_id="a", arm="x")
    with pytest.raises(ValueError, match="exactly one of answer or error"):
        AnswerRecord(atom_id="a", arm="x", answer="y", error="z")
    record = AnswerRecord(atom_id="a", arm="x", error="boom")
    assert not record.ok and record.to_dict()["error"] == "boom"
