from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.archi.utils.output_dataclass import PipelineOutput


def test_invoke_forwards_callbacks_to_agent_graph():
    observed = {}
    callback = object()

    class Graph:
        def invoke(self, inputs, config):
            observed["inputs"] = inputs
            observed["config"] = config
            return object()

    pipeline = BaseReActAgent.__new__(BaseReActAgent)
    pipeline.agent = Graph()
    pipeline._prepare_agent_inputs = lambda **kwargs: {"messages": ["question"]}
    pipeline._recursion_limit = lambda: 17
    pipeline._extract_messages = lambda output: []
    pipeline._metadata_from_agent_output = lambda output: {}
    pipeline._build_output_from_messages = (
        lambda messages, metadata: PipelineOutput(answer="answer")
    )

    output = pipeline.invoke(history=[("User", "question")], callbacks=[callback])

    assert output.answer == "answer"
    assert observed == {
        "inputs": {"messages": ["question"]},
        "config": {"recursion_limit": 17, "callbacks": [callback]},
    }
