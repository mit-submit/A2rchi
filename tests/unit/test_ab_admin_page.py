from flask import Flask
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.interfaces.chat_app.app import ChatWrapper, FlaskAppWrapper
from src.archi.pipelines.agents.agent_spec import load_agent_spec_from_text
from src.utils.config_service import StaticConfig
from src.utils.rbac import Permission


def _static_config_with_ab(comparison_rate=1.0):
    return StaticConfig(
        deployment_name="ab",
        config_version="1",
        data_path="/root/data",
        embedding_model="dummy",
        embedding_dimensions=384,
        chunk_size=500,
        chunk_overlap=50,
        distance_metric="cosine",
        global_config={"DATA_PATH": "/root/data", "ACCOUNTS_PATH": "/root/accounts"},
        services_config={
            "postgres": {"host": "localhost", "port": 5432},
            "chat_app": {
                "ab_testing": {
                    "enabled": True,
                    "comparison_rate": comparison_rate,
                    "pool": {
                        "champion": "baseline",
                        "variants": [
                            {"label": "baseline", "agent_spec": "baseline.md"},
                            {"label": "challenger", "agent_spec": "challenger.md"},
                        ],
                    },
                }
            },
        },
        data_manager_config={"sources": {}},
    )


def test_data_viewer_page_passes_ab_view_flag_to_template():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/data"):
        with patch.object(wrapper, "_can_view_ab_testing", return_value=True):
            with patch("src.interfaces.chat_app.app.render_template", return_value="ok") as render_template_mock:
                result = FlaskAppWrapper.data_viewer_page(wrapper)

    assert result == "ok"
    render_template_mock.assert_called_once_with("data.html", can_view_ab_testing=True)


def test_ab_testing_admin_page_requires_view_permission():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/admin/ab-testing"):
        with patch.object(wrapper, "_can_view_ab_testing", return_value=False):
            result = FlaskAppWrapper.ab_testing_admin_page(wrapper)

    assert result == ("Forbidden", 403)


def test_ab_testing_admin_page_renders_template_for_viewer():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/admin/ab-testing"):
        with patch.object(wrapper, "_can_view_ab_testing", return_value=True):
            with patch.object(wrapper, "_can_manage_ab_testing", return_value=False):
                with patch.object(wrapper, "_can_view_ab_metrics", return_value=True):
                    with patch("src.interfaces.chat_app.app.render_template", return_value="ok") as render_template_mock:
                        result = FlaskAppWrapper.ab_testing_admin_page(wrapper)

    assert result == "ok"
    render_template_mock.assert_called_once_with(
        "ab_testing.html",
        can_manage_ab_testing=False,
        can_view_ab_metrics=True,
    )


def test_ab_testing_admin_page_renders_template_for_admin():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app

    with app.test_request_context("/admin/ab-testing"):
        with patch.object(wrapper, "_can_view_ab_testing", return_value=True):
            with patch.object(wrapper, "_can_manage_ab_testing", return_value=True):
                with patch.object(wrapper, "_can_view_ab_metrics", return_value=True):
                    with patch("src.interfaces.chat_app.app.render_template", return_value="ok") as render_template_mock:
                        result = FlaskAppWrapper.ab_testing_admin_page(wrapper)

    assert result == "ok"
    render_template_mock.assert_called_once_with(
        "ab_testing.html",
        can_manage_ab_testing=True,
        can_view_ab_metrics=True,
    )


def test_can_view_ab_testing_allows_metrics_only_users():
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper._can_manage_ab_testing = Mock(return_value=False)

    with patch("src.interfaces.chat_app.app.has_permission", side_effect=lambda permission: permission == Permission.AB.METRICS):
        assert FlaskAppWrapper._can_view_ab_testing(wrapper) is True


def test_ab_testing_template_includes_theme_init_and_inline_agent_creation():
    template_path = Path(__file__).resolve().parents[2] / "src/interfaces/chat_app/templates/ab_testing.html"
    template = template_path.read_text()

    assert "modules/theme-init.js" in template
    assert 'id="ab-admin-create-agent"' not in template


def test_refresh_runtime_config_uses_local_static_config_snapshot_not_global_accessor():
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.config_service = Mock()
    wrapper.config_service.get_static_config.return_value = _static_config_with_ab(comparison_rate=0.4)
    wrapper.chat = Mock()

    with patch("src.interfaces.chat_app.app.get_full_config", side_effect=AssertionError("should not use get_full_config")):
        FlaskAppWrapper._refresh_runtime_config(wrapper)

    assert wrapper.services_config["chat_app"]["ab_testing"]["comparison_rate"] == 0.4
    assert wrapper.chat_app_config["ab_testing"]["comparison_rate"] == 0.4
    wrapper.chat.reload_static_state.assert_called_once()


def test_chat_reload_static_state_uses_local_static_config_snapshot_not_global_accessor():
    chat = object.__new__(ChatWrapper)
    chat.config_service = Mock()
    chat.config_service.get_static_config.return_value = _static_config_with_ab(comparison_rate=0.25)
    chat.refresh_ab_pool = Mock()

    with patch("src.interfaces.chat_app.app.get_full_config", side_effect=AssertionError("should not use get_full_config")):
        ChatWrapper.reload_static_state(chat)

    assert chat.services_config["chat_app"]["ab_testing"]["comparison_rate"] == 0.25
    assert chat.global_config["DATA_PATH"] == "/root/data"
    chat.refresh_ab_pool.assert_called_once()


def test_data_template_uses_labeled_header_actions_without_expand_collapse_buttons():
    template_path = Path(__file__).resolve().parents[2] / "src/interfaces/chat_app/templates/data.html"
    template = template_path.read_text()

    assert ">Uploader<" in template
    assert ">Postgres<" in template
    assert ">Refresh<" in template
    assert 'id="expand-all-btn"' not in template
    assert 'id="collapse-all-btn"' not in template


def test_build_admin_ab_pool_payload_exposes_current_runtime_pool_and_defaults():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.services_config = {
            "chat_app": {
                "default_provider": "openrouter",
                "default_model": "openai/gpt-4o",
                "ab_testing": {
                    "enabled": True,
                    "comparison_rate": 0.5,
                    "variant_label_mode": "post_vote_reveal",
                    "activity_panel_default_state": "hidden",
                    "max_pending_comparisons_per_conversation": 2,
                    "pool": {
                        "champion": "Baseline",
                        "variants": [
                        {"label": "Baseline", "agent_spec": "baseline-ab.md"},
                        {"label": "Poet", "agent_spec": "poet-ab.md"},
                    ],
                },
            },
        },
    }
    wrapper.config = {
        "data_manager": {
            "retrievers": {
                "hybrid_retriever": {
                    "num_documents_to_retrieve": 5,
                }
            }
        }
    }
    wrapper.global_config = {"DATA_PATH": "/root/data"}
    wrapper._is_admin_request = Mock(return_value=True)
    wrapper._can_view_ab_testing = Mock(return_value=True)
    wrapper._can_manage_ab_testing = Mock(return_value=True)
    wrapper._can_view_ab_metrics = Mock(return_value=True)
    wrapper._get_ab_participation_state = Mock(return_value={
        "can_participate": False,
        "eligible": False,
        "reason": "not_participant",
        "targeted": False,
    })
    wrapper.chat = SimpleNamespace(
        ab_pool=SimpleNamespace(
            enabled=True,
            champion_name="Baseline",
            sample_rate=0.5,
            disclosure_mode="post_vote_reveal",
            default_trace_mode="hidden",
            max_pending_per_conversation=2,
            comparison_rate=0.5,
            variant_label_mode="post_vote_reveal",
            activity_panel_default_state="hidden",
            max_pending_comparisons_per_conversation=2,
            variants=[
                SimpleNamespace(to_meta=lambda: {"label": "Baseline", "agent_spec": "baseline-ab.md"}),
                SimpleNamespace(to_meta=lambda: {"label": "Poet", "agent_spec": "poet-ab.md", "provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"}),
            ],
        ),
        ab_pool_state=SimpleNamespace(warnings=["A/B testing is active."]),
        ab_agent_import_diagnostics={"imported": 2, "conflicts": []},
    )

    with app.test_request_context("/api/ab/pool"):
        payload = FlaskAppWrapper._build_admin_ab_pool_payload(wrapper)

    assert payload["enabled"] is True
    assert payload["enabled_requested"] is True
    assert payload["champion"] == "Baseline"
    assert payload["variants"] == ["Baseline", "Poet"]
    assert payload["variant_label_mode"] == "post_vote_reveal"
    assert payload["activity_panel_default_state"] == "hidden"
    assert payload["defaults"]["ab_catalog_source"] == "database"
    assert payload["defaults"]["provider"] == "openrouter"
    assert payload["can_participate"] is False
    assert payload["participant_reason"] == "not_participant"
    assert payload["warnings"] == ["A/B testing is active."]
    assert payload["import_diagnostics"]["imported"] == 2


def test_list_ab_agents_returns_database_catalog_payload():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper._can_view_ab_testing = Mock(return_value=True)
    wrapper._get_ab_agent_spec_service = Mock(return_value=Mock(
        list_specs=Mock(return_value=[
            SimpleNamespace(name="Baseline", filename="baseline.md"),
            SimpleNamespace(name="Challenger", filename="challenger.md"),
        ])
    ))

    with app.test_request_context("/api/ab/agents/list"):
        response, status = FlaskAppWrapper.list_ab_agents(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["scope"] == "ab"
    assert payload["agents"] == [
        {"name": "Baseline", "filename": "baseline.md", "ab_only": True},
        {"name": "Challenger", "filename": "challenger.md", "ab_only": True},
    ]


def test_get_ab_agent_template_returns_structured_tool_catalog():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper._can_manage_ab_testing = Mock(return_value=True)
    wrapper._get_agent_tools = Mock(return_value=[
        {"name": "search_docs", "description": "Search indexed documents."},
        {"name": "lookup_ticket", "description": ""},
    ])

    with app.test_request_context("/api/ab/agents/template?name=Candidate"):
        response, status = FlaskAppWrapper.get_ab_agent_template(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["scope"] == "ab"
    assert payload["name"] == "Candidate"
    assert payload["prompt"] == "Write your system prompt here."
    assert payload["tools"] == [
        {"name": "search_docs", "description": "Search indexed documents."},
        {"name": "lookup_ticket", "description": ""},
    ]


def test_save_ab_agent_spec_uses_structured_payload_and_server_side_serialization():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper._can_manage_ab_testing = Mock(return_value=True)
    ab_service = Mock()
    ab_service.save_spec.return_value = SimpleNamespace(name="A/B: Candidate", filename="a-b-candidate.md")
    wrapper._get_ab_agent_spec_service = Mock(return_value=ab_service)

    with app.test_request_context(
        "/api/ab/agents",
        method="POST",
        json={
            "name": "A/B: Candidate",
            "tools": ["search_docs", "lookup_ticket"],
            "prompt": "Answer precisely.",
        },
    ):
        response, status = FlaskAppWrapper.save_ab_agent_spec(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["filename"] == "a-b-candidate.md"
    saved_content = ab_service.save_spec.call_args.args[0]
    parsed = load_agent_spec_from_text(saved_content)
    assert parsed.name == "A/B: Candidate"
    assert parsed.tools == ["search_docs", "lookup_ticket"]
    assert parsed.prompt == "Answer precisely."


def test_ab_get_pool_reports_untargeted_participant_state():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.services_config = {"chat_app": {"ab_testing": {"enabled": True, "comparison_rate": 0.5}}}
    wrapper.chat = SimpleNamespace(
        ab_pool=SimpleNamespace(
            enabled=True,
            sample_rate=0.5,
            is_targeted_user=Mock(return_value=False),
        )
    )
    wrapper._can_view_ab_testing = Mock(return_value=False)
    wrapper._can_manage_ab_testing = Mock(return_value=False)
    wrapper._can_view_ab_metrics = Mock(return_value=False)
    wrapper._is_admin_request = Mock(return_value=False)
    wrapper._get_effective_ab_sample_rate = Mock(return_value=0.5)
    wrapper._current_request_roles = Mock(return_value=["base-user"])
    wrapper._current_request_permissions = Mock(return_value=["ab:participate"])

    with app.test_request_context("/api/ab/pool"):
        with patch("src.interfaces.chat_app.app.has_permission", side_effect=lambda permission: permission == Permission.AB.PARTICIPATE):
            response, status = FlaskAppWrapper.ab_get_pool(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["can_participate"] is True
    assert payload["participant_eligible"] is False
    assert payload["participant_reason"] == "not_targeted"
    assert payload["participant_targeted"] is False
    assert payload["enabled"] is False


def test_ab_get_decision_distinguishes_non_participants():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.chat = SimpleNamespace(ab_pool=SimpleNamespace(enabled=True))

    with app.test_request_context("/api/ab/decision?client_id=tester"):
        with patch.object(wrapper, "_get_ab_participation_state", return_value={
            "can_participate": False,
            "eligible": False,
            "reason": "not_participant",
            "targeted": False,
        }):
            response, status = FlaskAppWrapper.ab_get_decision(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["reason"] == "not_participant"
    assert payload["use_ab"] is False


def test_save_agent_spec_ab_edit_is_rejected():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper._get_agent_scope = Mock(return_value="ab")
    wrapper._can_manage_ab_testing = Mock(return_value=True)
    ab_service = Mock()
    wrapper._get_ab_agent_spec_service = Mock(return_value=ab_service)

    with app.test_request_context(
        "/api/agents",
        method="POST",
        json={
            "scope": "ab",
            "mode": "edit",
            "existing_name": "Baseline",
            "content": "---\nname: Baseline\nab_only: true\n---\nUpdated prompt\n",
        },
    ):
        response, status = FlaskAppWrapper.save_agent_spec(wrapper)

    payload = response.get_json()
    assert status == 400
    assert "not supported" in payload["error"].lower()
    ab_service.save_spec.assert_not_called()


def test_save_agent_spec_ab_create_still_uses_database_catalog():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper._get_agent_scope = Mock(return_value="ab")
    wrapper._can_manage_ab_testing = Mock(return_value=True)
    ab_service = Mock()
    ab_service.save_spec.return_value = SimpleNamespace(name="Baseline Candidate", filename="baseline-candidate.md")
    wrapper._get_ab_agent_spec_service = Mock(return_value=ab_service)

    with app.test_request_context(
        "/api/agents",
        method="POST",
        json={
            "scope": "ab",
            "mode": "create",
            "content": "---\nname: Baseline Candidate\nab_only: true\n---\nPrompt\n",
        },
    ):
        response, status = FlaskAppWrapper.save_agent_spec(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["name"] == "Baseline Candidate"
    assert payload["filename"] == "baseline-candidate.md"
    ab_service.save_spec.assert_called_once()


def test_ab_admin_script_does_not_include_edit_agent_workflow():
    script_path = Path(__file__).resolve().parents[2] / "src/interfaces/chat_app/static/modules/ab-admin.js"
    script = script_path.read_text()

    assert "data-edit-agent" not in script
    assert "Save Edited Copy" not in script
    assert "openEditAgentModal" not in script


def test_ab_admin_script_uses_dedicated_ab_agent_endpoints():
    script_path = Path(__file__).resolve().parents[2] / "src/interfaces/chat_app/static/modules/ab-admin.js"
    script = script_path.read_text()

    assert "/api/ab/agents/list" in script
    assert "/api/ab/agents/template" in script
    assert "/api/ab/agents" in script
    assert "/api/agents/template?scope=ab" not in script


def test_ab_disable_pool_persists_disable_and_returns_admin_payload():
    app = Flask(__name__)
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.config_service = Mock()
    wrapper._can_manage_ab_testing = Mock(return_value=True)
    wrapper._refresh_runtime_config = Mock()
    wrapper._build_admin_ab_pool_payload = Mock(return_value={"enabled": False, "enabled_requested": False})

    with app.test_request_context("/api/ab/pool/disable", method="POST"):
        response, status = FlaskAppWrapper.ab_disable_pool(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["enabled"] is False
    assert payload["enabled_requested"] is False
    wrapper.config_service.update_services_config.assert_called_once_with({
        "chat_app": {
            "ab_testing": {
                "enabled": False,
            }
        }
    })
    wrapper._refresh_runtime_config.assert_called_once()
