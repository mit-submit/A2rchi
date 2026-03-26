"""
Unit tests for agent spec loading, parsing, and selection.

Tests cover:
- AgentSpec dataclass
- Frontmatter parsing (valid, empty, malformed)
- Metadata extraction (name, tools validation)
- File discovery (list_agent_files)
- Agent selection (select_agent_spec)
- In-memory loading (load_agent_spec_from_text)
- Filename slugification (slugify_agent_name)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Load agent_spec directly from its file path to avoid the heavy transitive
# dependencies pulled in by src.archi.pipelines.__init__ (LangChain, etc.).
_spec = importlib.util.spec_from_file_location(
    "agent_spec",
    str(
        Path(__file__).resolve().parents[2]
        / "src"
        / "archi"
        / "pipelines"
        / "agents"
        / "agent_spec.py"
    ),
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

AgentSpec = _mod.AgentSpec
AgentSpecError = _mod.AgentSpecError
list_agent_files = _mod.list_agent_files
load_agent_spec = _mod.load_agent_spec
load_agent_spec_from_text = _mod.load_agent_spec_from_text
select_agent_spec = _mod.select_agent_spec
slugify_agent_name = _mod.slugify_agent_name


VALID_AGENT_MD = """\
---
name: Test Agent
tools:
  - search_local_files
  - search_vectorstore_hybrid
---

You are a helpful test assistant.
"""

MINIMAL_AGENT_MD = """\
---
name: Minimal
tools:
  - search_local_files
---

Answer concisely.
"""

MCP_AGENT_MD = """\
---
name: MCP Research Agent
tools:
  - search_vectorstore_hybrid
  - fetch_catalog_document
  - mcp
---

You are a research-focused assistant.
Use vectorstore tools for internal docs first.
Use MCP tools for external checks when internal evidence is insufficient.
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agents_dir(tmp_path):
    """Create a temporary agents directory with sample spec files."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "01_default.md").write_text(VALID_AGENT_MD)
    (d / "02_minimal.md").write_text(MINIMAL_AGENT_MD)
    (d / "03_mcp.md").write_text(MCP_AGENT_MD)
    return d


@pytest.fixture
def single_agent_dir(tmp_path):
    """Directory with exactly one agent file."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "solo.md").write_text(VALID_AGENT_MD)
    return d


@pytest.fixture
def empty_agents_dir(tmp_path):
    """Directory that exists but contains no .md files."""
    d = tmp_path / "agents"
    d.mkdir()
    (d / "readme.txt").write_text("not an agent spec")
    return d


# ---------------------------------------------------------------------------
# AgentSpec dataclass
# ---------------------------------------------------------------------------


class TestAgentSpecDataclass:

    def test_fields_are_accessible(self):
        spec = AgentSpec(
            name="Demo",
            tools=["search_local_files"],
            prompt="You are helpful.",
            source_path=Path("/tmp/demo.md"),
        )
        assert spec.name == "Demo"
        assert spec.tools == ["search_local_files"]
        assert spec.prompt == "You are helpful."
        assert spec.source_path == Path("/tmp/demo.md")

    def test_frozen_prevents_mutation(self):
        spec = AgentSpec(
            name="Demo",
            tools=["search_local_files"],
            prompt="Prompt.",
            source_path=Path("/tmp/demo.md"),
        )
        with pytest.raises(AttributeError):
            spec.name = "Changed"


# ---------------------------------------------------------------------------
# load_agent_spec
# ---------------------------------------------------------------------------


class TestLoadAgentSpec:

    def test_loads_valid_spec(self, agents_dir):
        spec = load_agent_spec(agents_dir / "01_default.md")
        assert spec.name == "Test Agent"
        assert "search_local_files" in spec.tools
        assert "search_vectorstore_hybrid" in spec.tools
        assert "helpful test assistant" in spec.prompt

    def test_loads_mcp_spec(self, agents_dir):
        spec = load_agent_spec(agents_dir / "03_mcp.md")
        assert spec.name == "MCP Research Agent"
        assert "mcp" in spec.tools
        assert len(spec.tools) == 3

    def test_source_path_matches_input(self, agents_dir):
        path = agents_dir / "01_default.md"
        spec = load_agent_spec(path)
        assert spec.source_path == path

    def test_prompt_is_stripped(self, tmp_path):
        content = "---\nname: Padded\ntools:\n  - search_local_files\n---\n\n  Prompt with whitespace.  \n\n"
        f = tmp_path / "padded.md"
        f.write_text(content)
        spec = load_agent_spec(f)
        assert spec.prompt == "Prompt with whitespace."

    def test_tools_are_stripped(self, tmp_path):
        content = (
            "---\nname: Spaces\ntools:\n  - ' search_local_files '\n---\n\nPrompt body."
        )
        f = tmp_path / "spaces.md"
        f.write_text(content)
        spec = load_agent_spec(f)
        assert spec.tools == ["search_local_files"]


# ---------------------------------------------------------------------------
# load_agent_spec_from_text
# ---------------------------------------------------------------------------


class TestLoadAgentSpecFromText:

    def test_loads_from_string(self):
        spec = load_agent_spec_from_text(VALID_AGENT_MD)
        assert spec.name == "Test Agent"
        assert spec.tools == ["search_local_files", "search_vectorstore_hybrid"]
        assert "helpful test assistant" in spec.prompt

    def test_source_path_is_memory_sentinel(self):
        spec = load_agent_spec_from_text(VALID_AGENT_MD)
        assert spec.source_path == Path("<memory>")

    def test_round_trips_with_file_load(self, agents_dir):
        path = agents_dir / "01_default.md"
        from_file = load_agent_spec(path)
        from_text = load_agent_spec_from_text(path.read_text())
        assert from_file.name == from_text.name
        assert from_file.tools == from_text.tools
        assert from_file.prompt == from_text.prompt


# ---------------------------------------------------------------------------
# Frontmatter parsing edge cases
# ---------------------------------------------------------------------------


class TestFrontmatterParsing:

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        with pytest.raises(AgentSpecError, match="empty"):
            load_agent_spec(f)

    def test_missing_opening_fence_raises(self, tmp_path):
        f = tmp_path / "no_fence.md"
        f.write_text("name: Test\ntools:\n  - x\n---\nPrompt.")
        with pytest.raises(AgentSpecError, match="missing YAML frontmatter"):
            load_agent_spec(f)

    def test_missing_closing_fence_raises(self, tmp_path):
        f = tmp_path / "no_close.md"
        f.write_text("---\nname: Test\ntools:\n  - x\nPrompt without closing fence.")
        with pytest.raises(AgentSpecError, match="missing closing"):
            load_agent_spec(f)

    def test_empty_prompt_body_raises(self, tmp_path):
        f = tmp_path / "no_prompt.md"
        f.write_text("---\nname: Test\ntools:\n  - x\n---\n")
        with pytest.raises(AgentSpecError, match="prompt body is empty"):
            load_agent_spec(f)

    def test_invalid_yaml_raises(self, tmp_path):
        f = tmp_path / "bad_yaml.md"
        f.write_text("---\n: [invalid yaml\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="invalid YAML"):
            load_agent_spec(f)

    def test_leading_blank_lines_are_skipped(self, tmp_path):
        content = "\n\n\n---\nname: Blank\ntools:\n  - search_local_files\n---\n\nBody."
        f = tmp_path / "blanks.md"
        f.write_text(content)
        spec = load_agent_spec(f)
        assert spec.name == "Blank"

    def test_frontmatter_not_a_dict_raises(self, tmp_path):
        f = tmp_path / "list_fm.md"
        f.write_text("---\n- item1\n- item2\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="must be a mapping"):
            load_agent_spec(f)


# ---------------------------------------------------------------------------
# Metadata validation
# ---------------------------------------------------------------------------


class TestMetadataExtraction:

    def test_missing_name_raises(self, tmp_path):
        f = tmp_path / "no_name.md"
        f.write_text("---\ntools:\n  - search_local_files\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="name"):
            load_agent_spec(f)

    def test_non_string_name_raises(self, tmp_path):
        f = tmp_path / "int_name.md"
        f.write_text("---\nname: 123\ntools:\n  - search_local_files\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="name"):
            load_agent_spec(f)

    def test_missing_tools_raises(self, tmp_path):
        f = tmp_path / "no_tools.md"
        f.write_text("---\nname: NoTools\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="tools"):
            load_agent_spec(f)

    def test_empty_tools_list_raises(self, tmp_path):
        f = tmp_path / "empty_tools.md"
        f.write_text("---\nname: EmptyTools\ntools: []\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="tools"):
            load_agent_spec(f)

    def test_non_list_tools_raises(self, tmp_path):
        f = tmp_path / "str_tools.md"
        f.write_text("---\nname: StrTools\ntools: search_local_files\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="tools"):
            load_agent_spec(f)

    def test_tools_with_non_string_entry_raises(self, tmp_path):
        f = tmp_path / "mixed_tools.md"
        f.write_text(
            "---\nname: Mixed\ntools:\n  - search_local_files\n  - 42\n---\n\nPrompt."
        )
        with pytest.raises(AgentSpecError, match="tools"):
            load_agent_spec(f)

    def test_tool_with_only_whitespace_raises(self, tmp_path):
        f = tmp_path / "ws_tool.md"
        f.write_text("---\nname: WSTool\ntools:\n  - '   '\n---\n\nPrompt.")
        with pytest.raises(AgentSpecError, match="tools"):
            load_agent_spec(f)


# ---------------------------------------------------------------------------
# list_agent_files
# ---------------------------------------------------------------------------


class TestListAgentFiles:

    def test_returns_sorted_md_files(self, agents_dir):
        files = list_agent_files(agents_dir)
        assert len(files) == 3
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_ignores_non_md_files(self, agents_dir):
        (agents_dir / "notes.txt").write_text("ignore me")
        (agents_dir / "data.json").write_text("{}")
        files = list_agent_files(agents_dir)
        assert all(f.suffix == ".md" for f in files)

    def test_nonexistent_dir_raises(self, tmp_path):
        with pytest.raises(AgentSpecError, match="not found"):
            list_agent_files(tmp_path / "nonexistent")

    def test_file_path_instead_of_dir_raises(self, tmp_path):
        f = tmp_path / "file.md"
        f.write_text("content")
        with pytest.raises(AgentSpecError, match="not a directory"):
            list_agent_files(f)

    def test_empty_dir_returns_empty_list(self, empty_agents_dir):
        files = list_agent_files(empty_agents_dir)
        assert files == []


# ---------------------------------------------------------------------------
# select_agent_spec
# ---------------------------------------------------------------------------


class TestSelectAgentSpec:

    def test_selects_by_name(self, agents_dir):
        spec = select_agent_spec(agents_dir, agent_name="Minimal")
        assert spec.name == "Minimal"

    def test_selects_first_when_no_name_given(self, agents_dir):
        spec = select_agent_spec(agents_dir)
        assert spec.name == "Test Agent"  # 01_default.md is first lexicographically

    def test_unknown_name_raises(self, agents_dir):
        with pytest.raises(AgentSpecError, match="not found"):
            select_agent_spec(agents_dir, agent_name="Does Not Exist")

    def test_empty_dir_raises(self, empty_agents_dir):
        with pytest.raises(AgentSpecError, match="No agent markdown files"):
            select_agent_spec(empty_agents_dir)

    def test_single_agent_returns_it(self, single_agent_dir):
        spec = select_agent_spec(single_agent_dir)
        assert spec.name == "Test Agent"


# ---------------------------------------------------------------------------
# slugify_agent_name
# ---------------------------------------------------------------------------


class TestSlugifyAgentName:

    def test_simple_name(self):
        assert slugify_agent_name("CMS CompOps") == "cms-compops.md"

    def test_special_characters(self):
        result = slugify_agent_name("Agent: V2 (beta)")
        assert result.endswith(".md")
        assert " " not in result
        assert ":" not in result
        assert "(" not in result

    def test_leading_trailing_whitespace(self):
        result = slugify_agent_name("  My Agent  ")
        assert result == "my-agent.md"

    def test_empty_string_returns_default(self):
        assert slugify_agent_name("") == "agent.md"

    def test_only_special_chars_returns_default(self):
        assert slugify_agent_name("!!!@@@") == "agent.md"

    def test_numeric_name(self):
        result = slugify_agent_name("Agent 42")
        assert result == "agent-42.md"

    def test_consecutive_specials_collapse(self):
        result = slugify_agent_name("a---b___c")
        assert result == "a-b-c.md"
