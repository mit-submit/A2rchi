# isort: skip_file
from types import SimpleNamespace

import pytest

from src.evaluation.qa import runtime
from src.evaluation.qa.runtime import (
    ArchiAgentRuntime,
    LangChainEvaluatorRuntime,
    ToolTimingCallback,
)
from src.evaluation.qa.profile import load_profile
from src.evaluation.qa.validation import Atom


class _FakePipeline:
    pass


def _files(tmp_path, config_extra="", tools="search"):
    config = tmp_path / "agent.yaml"
    config.write_text(
        "services:\n"
        "  chat_app:\n"
        "    agent_class: FakePipeline\n"
        "    default_provider: openai\n"
        "    default_model: gpt-test\n"
        f"{config_extra}"
    )
    spec = tmp_path / "agent.md"
    spec.write_text(f"---\nname: Test\ntools: [{tools}]\n---\nAnswer directly.\n")
    return config, spec


def test_load_agent_inputs_uses_selected_config_and_spec(monkeypatch, tmp_path):
    config, spec = _files(tmp_path)
    monkeypatch.setattr(
        runtime,
        "import_module",
        lambda name: SimpleNamespace(FakePipeline=_FakePipeline),
    )

    loaded_config, loaded_spec, spec_text, pipeline_class = runtime.load_agent_inputs(
        config, spec
    )

    assert loaded_config["services"]["chat_app"]["default_model"] == "gpt-test"
    assert loaded_spec.tools == ["search"]
    assert "Answer directly." in spec_text
    assert pipeline_class is _FakePipeline


def test_load_agent_inputs_does_not_police_admin_config_content(
    monkeypatch, tmp_path
):
    config, spec = _files(
        tmp_path,
        "    api_key: sk-production-secret\n"
        "    agents_dir: /tmp/agents\n",
    )
    spec.write_text(
        "---\nname: Test\ntools: [search]\n---\nUse sk-production-secret.\n"
    )
    monkeypatch.setattr(
        runtime,
        "import_module",
        lambda name: SimpleNamespace(FakePipeline=_FakePipeline),
    )

    loaded_config, _, spec_text, _ = runtime.load_agent_inputs(config, spec)

    assert loaded_config["services"]["chat_app"]["api_key"] == "sk-production-secret"
    assert loaded_config["services"]["chat_app"]["agents_dir"] == "/tmp/agents"
    assert "sk-production-secret" in spec_text


def test_evaluator_uses_structured_output_schema():
    observed = {}

    class Runnable:
        def invoke(self, messages):
            observed["messages"] = messages
            return {"atoms": []}

    class Model:
        def with_structured_output(self, schema):
            observed["schema"] = schema
            return Runnable()

    result = LangChainEvaluatorRuntime._structured(
        Model(), {"type": "object"}, "system prompt", {"question": "q"}
    )

    assert result == {"atoms": []}
    assert observed["schema"] == {"type": "object"}


def test_evaluator_compares_complete_answer_to_gold_atoms():
    invocations = []

    class Runnable:
        def invoke(self, messages):
            invocations.append(messages)
            return {
                "judgments": [
                    {"atom_id": "g1", "outcome": "entailed", "rationale": "match"}
                ]
            }

    class Model:
        def with_structured_output(self, schema):
            assert set(schema["properties"]["judgments"]["items"]["properties"]) == {
                "atom_id",
                "outcome",
                "rationale",
            }
            return Runnable()

    models = []

    def model_factory(provider, model, provider_config, **kwargs):
        instance = Model()
        models.append(instance)
        return instance

    evaluator = LangChainEvaluatorRuntime(load_profile(None), model_factory)
    result = evaluator.compare(
        "question",
        [Atom(id="g1", text="expected", required=True)],
        "complete answer",
    )

    assert result["judgments"][0]["outcome"] == "entailed"
    assert '"answer": "complete answer"' in invocations[0][1][1]
    assert "response_atoms" not in invocations[0][1][1]


def test_evaluator_fails_when_model_rejects_temperature():
    calls = []

    def model_factory(provider, model, provider_config, **kwargs):
        calls.append(kwargs)
        raise TypeError("unexpected keyword argument 'temperature'")

    with pytest.raises(TypeError, match="unexpected keyword argument 'temperature'"):
        LangChainEvaluatorRuntime(load_profile(None), model_factory)

    assert calls == [{"temperature": 0}]


def test_evaluator_requires_zero_temperature():
    calls = []

    def model_factory(provider, model, provider_config, **kwargs):
        calls.append(kwargs)
        return object()

    LangChainEvaluatorRuntime(load_profile(None), model_factory)

    assert calls == [{"temperature": 0}, {"temperature": 0}]


class _Output:
    def __init__(self, answer):
        self.answer = answer


def test_tool_timing_callback_records_success_and_error(monkeypatch):
    from uuid import UUID

    clock = {"now": 10.0}
    monkeypatch.setattr(runtime, "perf_counter", lambda: clock["now"])
    callback = ToolTimingCallback()
    first_run = UUID("00000000-0000-0000-0000-000000000001")
    second_run = UUID("00000000-0000-0000-0000-000000000002")

    callback.on_tool_start({"name": "search"}, "query", run_id=first_run)
    clock["now"] = 10.125
    callback.on_tool_end("result", run_id=first_run)
    callback.on_tool_start({"name": "lookup"}, "id", run_id=second_run)
    clock["now"] = 10.5
    callback.on_tool_error(RuntimeError("failed"), run_id=second_run)

    assert callback.timings == [
        {
            "ordinal": 1,
            "name": "search",
            "status": "success",
            "duration_ms": 125,
        },
        {
            "ordinal": 2,
            "name": "lookup",
            "status": "error",
            "duration_ms": 375,
        },
    ]


def test_tool_timing_callback_integrates_with_langchain_tool():
    from langchain_core.tools import tool

    @tool
    def double(value: int) -> int:
        """Double one integer."""
        return value * 2

    callback = ToolTimingCallback()

    assert double.invoke({"value": 4}, config={"callbacks": [callback]}) == 8
    assert callback.timings[0]["ordinal"] == 1
    assert callback.timings[0]["name"] == "double"
    assert callback.timings[0]["status"] == "success"
    assert isinstance(callback.timings[0]["duration_ms"], int)
    assert callback.timings[0]["duration_ms"] >= 0


def _config():
    return {
        "services": {
            "chat_app": {"default_provider": "fake", "default_model": "fake-model"}
        }
    }


def test_archi_runtime_creates_fresh_pipeline_per_attempt():
    class Pipeline:
        instances = 0

        def __init__(self, **kwargs):
            Pipeline.instances += 1

        def invoke(self, **kwargs):
            return _Output("final answer")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline)

    assert agent.run("question") == "final answer"
    assert agent.run("question") == "final answer"
    assert Pipeline.instances == 2


def test_archi_runtime_reports_vectorstore_failure_from_attempt(monkeypatch):
    spec = SimpleNamespace(tools=["search_vectorstore_hybrid"])

    def fail_vectorstore(self):
        raise RuntimeError("vector store is offline")

    monkeypatch.setattr(ArchiAgentRuntime, "_load_vectorstore", fail_vectorstore)

    agent = ArchiAgentRuntime(_config(), spec, object)

    with pytest.raises(RuntimeError, match="vector store is offline"):
        agent.run("question")


def test_archi_runtime_uses_normal_pipeline_invocation(monkeypatch):
    vectorstore = object()
    observed = {}

    class Pipeline:
        def __init__(self, **kwargs):
            observed["init"] = kwargs

        def invoke(self, **kwargs):
            observed["invoke"] = kwargs
            return _Output("final answer")

    monkeypatch.setattr(
        ArchiAgentRuntime, "_load_vectorstore", lambda self: vectorstore
    )
    answer = ArchiAgentRuntime(
        _config(),
        SimpleNamespace(tools=["search_vectorstore_hybrid"]),
        Pipeline,
    ).run("question")

    assert answer == "final answer"
    assert "strict_tool_loading" not in observed["init"]
    assert observed["invoke"]["history"] == [("User", "question")]
    assert observed["invoke"]["vectorstore"] is vectorstore
    assert len(observed["invoke"]["callbacks"]) == 1
    assert isinstance(observed["invoke"]["callbacks"][0], ToolTimingCallback)


def test_archi_runtime_collects_tool_timings(monkeypatch):
    from uuid import UUID

    ticks = iter((3.0, 3.125))
    monkeypatch.setattr(runtime, "perf_counter", lambda: next(ticks))

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            callback = kwargs["callbacks"][0]
            run_id = UUID("00000000-0000-0000-0000-000000000003")
            callback.on_tool_start({"name": "search"}, "query", run_id=run_id)
            callback.on_tool_end("result", run_id=run_id)
            return _Output("final answer")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline)

    assert agent.run("question") == "final answer"
    assert agent.tool_calls == [
        {
            "ordinal": 1,
            "name": "search",
            "status": "success",
            "duration_ms": 125,
        }
    ]


def test_archi_runtime_fails_before_model_when_selected_mcp_tools_did_not_load():
    class Pipeline:
        def __init__(self, **kwargs):
            self.loaded_mcp_tools = []

        def invoke(self, **kwargs):
            raise AssertionError("model must not run without selected MCP tools")

    with pytest.raises(RuntimeError, match="selected 'mcp'.*no MCP tools"):
        ArchiAgentRuntime(_config(), SimpleNamespace(tools=["mcp"]), Pipeline).run(
            "question"
        )


def test_archi_runtime_invokes_model_when_selected_mcp_tools_loaded():
    class Pipeline:
        def __init__(self, **kwargs):
            self.loaded_mcp_tools = [SimpleNamespace(name="search")]

        def invoke(self, **kwargs):
            return _Output("grounded answer")

    answer = ArchiAgentRuntime(_config(), SimpleNamespace(tools=["mcp"]), Pipeline).run(
        "question"
    )

    assert answer == "grounded answer"


def test_archi_runtime_rejects_empty_answer():
    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            return _Output("")

    with pytest.raises(ValueError, match="no usable terminal answer"):
        ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline).run(
            "question"
        )
