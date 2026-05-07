from types import SimpleNamespace
from unittest.mock import Mock

from src.utils.ab_agent_spec_service import ABAgentSpecService


def test_insert_version_accepts_realdictcursor_row():
    service = object.__new__(ABAgentSpecService)
    cursor = Mock()
    cursor.fetchone.return_value = {"version_id": 7}
    spec = SimpleNamespace(
        name="Candidate",
        tools=["search"],
        prompt="Prompt",
        ab_only=True,
    )

    version_id = ABAgentSpecService._insert_version(
        service,
        cursor,
        spec_id=1,
        version_number=1,
        spec=spec,
        content="---\nname: Candidate\nab_only: true\n---\nPrompt\n",
        content_hash="abc",
        prompt_hash="def",
        source_type="import",
        source_path="/tmp/candidate.md",
        created_by="system",
    )

    assert version_id == 7
    cursor.execute.assert_called_once()
