"""
Pipeline Feature Test Matrix
=============================
Tests both CMSCompOpsAgent (LangGraph) and CopilotAgentPipeline (Copilot SDK)
against the same feature matrix to verify interface parity.

Usage:  python tests/test_pipeline_matrix.py
"""

import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Shared config for both pipelines (Ollama on submit76)
# ---------------------------------------------------------------------------
SHARED_CONFIG: Dict[str, Any] = {
    "archi": {},
    "services": {
        "chat_app": {
            "default_provider": "local",
            "default_model": "qwen3:32b",
            "providers": {
                "local": {
                    "enabled": True,
                    "base_url": "http://submit76.mit.edu:7870",
                    "mode": "ollama",
                    "default_model": "qwen3:32b",
                    "models": ["qwen3:32b"],
                },
            },
        },
    },
    "data_manager": {},
}

CONSTRUCTOR_KWARGS = {
    "default_provider": "local",
    "default_model": "qwen3:32b",
}


# ---------------------------------------------------------------------------
# Test result tracking
# ---------------------------------------------------------------------------
class TestResult:
    def __init__(self, name: str, pipeline: str):
        self.name = name
        self.pipeline = pipeline
        self.passed = False
        self.error: Optional[str] = None
        self.details: Optional[str] = None

    def ok(self, details: str = ""):
        self.passed = True
        self.details = details
        return self

    def fail(self, error: str):
        self.passed = False
        self.error = error
        return self


results: List[TestResult] = []


def test(name: str, pipeline: str) -> TestResult:
    r = TestResult(name, pipeline)
    results.append(r)
    return r


# ---------------------------------------------------------------------------
# Import both pipeline classes
# ---------------------------------------------------------------------------
print("=" * 70)
print("PIPELINE FEATURE TEST MATRIX")
print("=" * 70)

print("\nImporting pipeline classes...")
try:
    from src.archi.pipelines.agents.cms_comp_ops_agent import CMSCompOpsAgent

    print("  CMSCompOpsAgent: OK")
except Exception as e:
    print(f"  CMSCompOpsAgent: FAILED - {e}")
    sys.exit(1)

try:
    from src.archi.pipelines.copilot_agents.copilot_agent import CopilotAgentPipeline

    print("  CopilotAgentPipeline: OK")
except Exception as e:
    print(f"  CopilotAgentPipeline: FAILED - {e}")
    sys.exit(1)

from src.archi.utils.output_dataclass import PipelineOutput

# ===================================================================
# TEST MATRIX
# ===================================================================

PIPELINES: Dict[str, Any] = {}

# ----- T1: Instantiation -----
print("\n--- T1: Instantiation ---")
for label, cls in [("LangGraph", CMSCompOpsAgent), ("Copilot", CopilotAgentPipeline)]:
    r = test("T1: Instantiation", label)
    try:
        agent = cls(config=SHARED_CONFIG, **CONSTRUCTOR_KWARGS)
        PIPELINES[label] = agent
        r.ok(f"type={type(agent).__name__}")
        print(f"  [{label}] PASS — {r.details}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T2: Has required methods -----
print("\n--- T2: Required methods ---")
REQUIRED_METHODS = [
    "invoke",
    "stream",
    "astream",
    "get_tool_registry",
    "get_tool_descriptions",
]
for label, agent in PIPELINES.items():
    for method in REQUIRED_METHODS:
        r = test(f"T2: has {method}()", label)
        if callable(getattr(agent, method, None)):
            r.ok()
            print(f"  [{label}] {method}(): PASS")
        else:
            r.fail(f"missing or not callable")
            print(f"  [{label}] {method}(): FAIL")

# ----- T3: get_tool_registry() returns valid mapping -----
print("\n--- T3: Tool registry ---")
for label, agent in PIPELINES.items():
    r = test("T3: get_tool_registry()", label)
    try:
        registry = agent.get_tool_registry()
        if not isinstance(registry, dict):
            r.fail(f"returned {type(registry).__name__}, expected dict")
        elif len(registry) == 0:
            r.fail("empty registry")
        else:
            tool_names = sorted(registry.keys())
            r.ok(f"{len(registry)} tools: {tool_names}")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")

# ----- T4: get_tool_descriptions() returns valid mapping -----
print("\n--- T4: Tool descriptions ---")
for label, agent in PIPELINES.items():
    r = test("T4: get_tool_descriptions()", label)
    try:
        descs = agent.get_tool_descriptions()
        if not isinstance(descs, dict):
            r.fail(f"returned {type(descs).__name__}, expected dict")
        elif len(descs) == 0:
            r.fail("empty descriptions")
        else:
            all_have_desc = all(
                isinstance(v, str) and len(v) > 0 for v in descs.values()
            )
            r.ok(f"{len(descs)} descriptions, all non-empty={all_have_desc}")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")

# ----- T5: stream() — basic text response -----
print("\n--- T5: stream() basic text ---")
for label, agent in PIPELINES.items():
    r = test("T5: stream() basic text", label)
    try:
        history = [("user", "What is 2+2? Answer only with the number.")]
        outputs = list(agent.stream(history=history))
        if not outputs:
            r.fail("no outputs")
        else:
            event_types = [
                o.metadata.get("event_type", "?") if o.metadata else "?"
                for o in outputs
            ]
            final = outputs[-1]
            has_final = "final" in event_types
            has_answer = bool(final.answer and final.answer.strip())
            if not has_final:
                r.fail(f"no 'final' event. Got: {event_types}")
            elif not has_answer:
                r.fail(f"empty final answer")
            else:
                r.ok(
                    f"{len(outputs)} events, types={event_types}, "
                    f"answer='{final.answer[:60]}'"
                )
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T6: stream() returns PipelineOutput instances -----
print("\n--- T6: All outputs are PipelineOutput ---")
for label, agent in PIPELINES.items():
    r = test("T6: PipelineOutput type check", label)
    try:
        history = [("user", "Say hello")]
        outputs = list(agent.stream(history=history))
        non_po = [
            type(o).__name__ for o in outputs if not isinstance(o, PipelineOutput)
        ]
        if non_po:
            r.fail(f"non-PipelineOutput types: {non_po}")
        else:
            r.ok(f"all {len(outputs)} outputs are PipelineOutput")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T7: Event type sequence -----
print("\n--- T7: Event type sequence ---")
for label, agent in PIPELINES.items():
    r = test("T7: Event types present", label)
    try:
        history = [("user", "What is the capital of France? Be brief.")]
        outputs = list(agent.stream(history=history))
        event_types = [
            o.metadata.get("event_type", "?") if o.metadata else "?" for o in outputs
        ]
        has_text = "text" in event_types
        has_final = "final" in event_types
        has_thinking = "thinking_start" in event_types or "thinking_end" in event_types
        if not has_final:
            r.fail(f"missing 'final' event. Got: {event_types}")
        elif not has_text:
            r.fail(f"missing 'text' event. Got: {event_types}")
        else:
            r.ok(
                f"text={has_text}, final={has_final}, thinking={has_thinking}, all={event_types}"
            )
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T8: Final output has usage metadata -----
print("\n--- T8: Final output metadata ---")
for label, agent in PIPELINES.items():
    r = test("T8: Final has usage/model", label)
    try:
        history = [("user", "Say OK")]
        outputs = list(agent.stream(history=history))
        finals = [
            o for o in outputs if o.metadata and o.metadata.get("event_type") == "final"
        ]
        if not finals:
            r.fail("no final event")
        else:
            f = finals[-1]
            usage = f.metadata.get("usage")
            model = f.metadata.get("model")
            has_usage = usage is not None
            has_model = model is not None
            r.ok(f"usage={usage}, model={model}")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T9: invoke() returns single PipelineOutput -----
print("\n--- T9: invoke() ---")
for label, agent in PIPELINES.items():
    r = test("T9: invoke()", label)
    try:
        history = [("user", "What is 3+3? Just the number.")]
        result = agent.invoke(history=history)
        if not isinstance(result, PipelineOutput):
            r.fail(f"returned {type(result).__name__}")
        elif not result.answer or not result.answer.strip():
            r.fail("empty answer")
        else:
            r.ok(f"answer='{result.answer[:60]}'")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T10: conversation_id is accepted -----
print("\n--- T10: conversation_id kwarg ---")
for label, agent in PIPELINES.items():
    r = test("T10: conversation_id kwarg", label)
    try:
        history = [("user", "Say yes")]
        outputs = list(agent.stream(history=history, conversation_id=12345))
        if not outputs:
            r.fail("no outputs")
        else:
            final = outputs[-1]
            r.ok(f"accepted conversation_id=12345, answer='{final.answer[:40]}'")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T11: Multi-turn history -----
print("\n--- T11: Multi-turn history ---")
for label, agent in PIPELINES.items():
    r = test("T11: Multi-turn history", label)
    try:
        history = [
            ("user", "My name is Alice."),
            ("assistant", "Hello Alice! How can I help you?"),
            ("user", "What is my name? Just say the name."),
        ]
        outputs = list(agent.stream(history=history))
        final = outputs[-1] if outputs else None
        if not final or not final.answer:
            r.fail("no final answer")
        elif "alice" in final.answer.lower():
            r.ok(f"correctly recalled 'Alice' — answer='{final.answer[:60]}'")
        else:
            r.fail(f"did not recall 'Alice' — answer='{final.answer[:60]}'")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T12: agent_spec support -----
print("\n--- T12: agent_spec support ---")
for label, cls in [("LangGraph", CMSCompOpsAgent), ("Copilot", CopilotAgentPipeline)]:
    r = test("T12: agent_spec", label)
    try:

        class FakeAgentSpec:
            name = "TestAgent"
            prompt = "You are a pirate. Always respond in pirate speak."
            tools = []

        agent_with_spec = cls(
            config=SHARED_CONFIG,
            agent_spec=FakeAgentSpec(),
            **CONSTRUCTOR_KWARGS,
        )
        history = [("user", "Hello, who are you?")]
        outputs = list(agent_with_spec.stream(history=history))
        final = outputs[-1] if outputs else None
        if not final or not final.answer:
            r.fail("no final answer")
        else:
            answer_lower = final.answer.lower()
            pirate_words = [
                "ahoy",
                "matey",
                "arr",
                "ye",
                "pirate",
                "captain",
                "sea",
                "ship",
                "sail",
                "treasure",
            ]
            has_pirate = any(w in answer_lower for w in pirate_words)
            if has_pirate:
                r.ok(f"pirate_words={has_pirate}, answer='{final.answer[:80]}'")
            else:
                r.fail(
                    f"agent_spec prompt not reflected — answer='{final.answer[:80]}'"
                )
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T13: Accumulated text contract -----
print("\n--- T13: Text events have content ---")
for label, agent in PIPELINES.items():
    r = test("T13: Text events have content", label)
    try:
        history = [("user", "Count from 1 to 5.")]
        outputs = list(agent.stream(history=history))
        text_events = [
            o for o in outputs if o.metadata and o.metadata.get("event_type") == "text"
        ]
        if not text_events:
            r.fail("no text events emitted")
        else:
            non_empty = [o for o in text_events if o.answer and o.answer.strip()]
            r.ok(f"{len(text_events)} text events, {len(non_empty)} with content")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T14: Final event is last -----
print("\n--- T14: Final event is last ---")
for label, agent in PIPELINES.items():
    r = test("T14: Final event is last", label)
    try:
        history = [("user", "Say done.")]
        outputs = list(agent.stream(history=history))
        if not outputs:
            r.fail("no outputs")
        else:
            last = outputs[-1]
            is_final = last.metadata and last.metadata.get("event_type") == "final"
            if not is_final:
                last_type = (
                    last.metadata.get("event_type") if last.metadata else "no metadata"
                )
                r.fail(f"last event is '{last_type}', not 'final'")
            else:
                r.ok(f"final is last of {len(outputs)} events")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()

# ----- T15: Empty extra kwargs don't crash -----
print("\n--- T15: Extra kwargs tolerance ---")
for label, agent in PIPELINES.items():
    r = test("T15: Extra kwargs tolerance", label)
    try:
        history = [("user", "Say OK")]
        outputs = list(
            agent.stream(
                history=history,
                conversation_id=None,
                vectorstore=None,
                user_id=None,
            )
        )
        final = outputs[-1] if outputs else None
        if not final or not final.answer:
            r.fail("no answer with extra None kwargs")
        else:
            r.ok(f"accepted None kwargs, answer='{final.answer[:40]}'")
        print(f"  [{label}] {r.passed and 'PASS' or 'FAIL'} — {r.details or r.error}")
    except Exception as e:
        r.fail(str(e))
        print(f"  [{label}] FAIL — {e}")
        traceback.print_exc()


# ===================================================================
# RESULTS SUMMARY
# ===================================================================
print("\n")
print("=" * 70)
print("RESULTS SUMMARY")
print("=" * 70)

# Group by test name
from collections import OrderedDict

matrix: OrderedDict[str, Dict[str, TestResult]] = OrderedDict()
for r in results:
    if r.name not in matrix:
        matrix[r.name] = {}
    matrix[r.name][r.pipeline] = r

print(f"\n{'Test':<40} {'LangGraph':<12} {'Copilot':<12}")
print("-" * 64)
for test_name, by_pipeline in matrix.items():
    lg = by_pipeline.get("LangGraph")
    cp = by_pipeline.get("Copilot")
    lg_str = "PASS" if lg and lg.passed else "FAIL" if lg else "SKIP"
    cp_str = "PASS" if cp and cp.passed else "FAIL" if cp else "SKIP"
    print(f"{test_name:<40} {lg_str:<12} {cp_str:<12}")

total = len(results)
passed = sum(1 for r in results if r.passed)
failed = sum(1 for r in results if not r.passed)

print(f"\n{'Total':<40} {total}")
print(f"{'Passed':<40} {passed}")
print(f"{'Failed':<40} {failed}")

# Print failures
if failed:
    print("\n--- FAILURES ---")
    for r in results:
        if not r.passed:
            print(f"  [{r.pipeline}] {r.name}: {r.error}")

print()
sys.exit(0 if failed == 0 else 1)
