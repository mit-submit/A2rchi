from click.testing import CliRunner

import src.cli.qa_eval as qa_cli_module
import src.evaluation.qa.workflow as workflow_module
from src.cli.qa_eval import eval_cli
from src.evaluation.qa.artifacts import read_json
from src.evaluation.qa.workflow import QAWorkflow


class _Workflow:
    def __init__(self, calls):
        self.calls = calls

    def composite(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run-1", "status": "scored"}


def test_composite_cli_uses_prd_option_shape(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(qa_cli_module, "QAWorkflow", lambda: _Workflow(calls))

    result = CliRunner().invoke(
        eval_cli,
        [
            "qa",
            "--dataset",
            str(tmp_path / "data.json"),
            "--agent-config",
            str(tmp_path / "agent.yaml"),
            "--agent-spec",
            str(tmp_path / "agent.md"),
            "--output-dir",
            str(tmp_path / "run"),
            "-n",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "QA evaluation completed: run-1 (scored)\n"
    assert calls[0]["attempts"] == 4
    assert calls[0]["dataset"] == tmp_path / "data.json"


def test_composite_cli_reports_missing_required_flags():
    result = CliRunner().invoke(eval_cli, ["qa"])

    assert result.exit_code == 2
    assert (
        "requires --dataset, --agent-config, --agent-spec, --output-dir"
        in result.output
    )


def test_composite_cli_runs_local_dataset_to_report_with_four_attempts(
    monkeypatch, tmp_path
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '[{"id":"item","question":"Q","expected_answer":"A",'
        '"freshness":"static","expected_atoms":[{"id":"g1",'
        '"text":"A","required":true}]}]'
    )

    class Evaluator:
        def compare(self, question, gold_atoms, answer):
            return {
                "judgments": [
                    {
                        "atom_id": "g1",
                        "outcome": "entailed",
                        "rationale": "match",
                    }
                ]
            }

    class Agent:
        def run(self, question):
            return "A"

    config = {
        "services": {
            "chat_app": {
                "agent_class": "FakeAgent",
                "default_provider": "fake",
                "default_model": "fake-model",
            }
        }
    }
    monkeypatch.setattr(
        workflow_module,
        "load_agent_inputs",
        lambda config_path, spec_path: (
            config,
            object(),
            "---\nname: Fake\ntools: [fake]\n---\nPrompt\n",
            object,
        ),
    )
    monkeypatch.setattr(
        qa_cli_module,
        "QAWorkflow",
        lambda: QAWorkflow(lambda profile: Evaluator(), lambda *args: Agent()),
    )
    run_dir = tmp_path / "run"

    result = CliRunner().invoke(
        eval_cli,
        [
            "qa",
            "--dataset",
            str(dataset),
            "--agent-config",
            str(tmp_path / "agent.yaml"),
            "--agent-spec",
            str(tmp_path / "agent.md"),
            "--output-dir",
            str(run_dir),
            "-n",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (run_dir / "report.md").exists()
    summary = read_json(run_dir / "summary.json")
    assert summary["quality_accounted_attempts"] == 4
    assert summary["overall_attempt_pass_rate"] == 1.0
