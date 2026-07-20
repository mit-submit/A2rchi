"""Tests that the attachments config block renders from base-config.yaml."""
from pathlib import Path

import yaml
from jinja2 import Environment

TEMPLATE = Path("src/cli/templates/base-config.yaml")


def _render(user_cfg: dict) -> dict:
    env = Environment()
    template = env.from_string(TEMPLATE.read_text())
    rendered = template.render(**user_cfg)
    return yaml.safe_load(rendered)


def _minimal_user_cfg() -> dict:
    # Mirrors the minimal vars the template dereferences before chat_app.
    #
    # NOTE: the template is normally rendered with Jinja's ChainableUndefined
    # (see src/cli/cli_main.py), which lets `a.b.c` chains stay Undefined all
    # the way down without raising. This test uses the plain Environment()
    # (default Undefined), which raises as soon as a *middle* hop in a
    # dotted chain (e.g. `services.chat_app.auth.enabled`) is missing. So,
    # unlike the real deployment path, every intermediate mapping the
    # template dereferences before (and around) the attachments block has to
    # be stubbed in as an actual (possibly empty) dict here - only the final
    # hop of each chain is allowed to be genuinely absent.
    return {
        "name": "t",
        "global": {"LOGGING": {}},
        "services": {
            "benchmarking": {
                "mode_settings": {"sources_settings": {}, "ragas_settings": {}},
                "ragas_settings": {},
            },
            "piazza": {},
            "mattermost": {},
            "redmine_mailbox": {},
            "jira_ticket_responder": {},
            "postgres": {},
            "chat_app": {
                "auth": {"sso": {"client_kwargs": {}}, "basic": {}},
                "alerts": {},
                "attachments": {},
            },
            "data_manager": {"auth": {}},
            "grader_app": {"prompts": {"grading": {}, "image_processing": {}}},
            "vectorstore": {},
            "grafana": {},
        },
        "archi": {},
        "data_manager": {
            "embedding_class_map": {
                "OpenAIEmbeddings": {"kwargs": {}},
                "HuggingFaceEmbeddings": {
                    "kwargs": {"model_kwargs": {}, "encode_kwargs": {}}
                },
            },
            "stemming": {},
            "retrievers": {
                "semantic_retriever": {},
                "bm25_retriever": {},
                "hybrid_retriever": {},
            },
            "sources": {
                "local_files": {},
                "links": {
                    "html_scraper": {},
                    "selenium_scraper": {
                        "selenium_scraper": {},
                        "selenium_class_map": {"CERNSSOScraper": {"kwargs": {}}},
                    },
                },
                "git": {},
                "sso": {},
                "jira": {},
                "redmine": {},
                "indico": {
                    "sso_kwargs": {},
                    "slide_conversion": {},
                    "plot_extraction": {},
                },
            },
            "utils": {"anonymizer": {}},
        },
        "utils": {"postgres": {}},
    }


def test_attachments_defaults_render():
    # Defaults track Claude/ChatGPT attachment limits (30 MB/file, 20/chat).
    cfg = _render(_minimal_user_cfg())
    att = cfg["services"]["chat_app"]["attachments"]
    assert att["enabled"] is True
    assert att["max_file_mb"] == 30
    assert att["max_per_conversation"] == 20
    assert att["max_total_mb_per_user"] == 512
    assert att["abandoned_conversation_ttl_hours"] == 72
    assert att["text_budget_chars"] == 400000
    assert att["text_poor_page_chars"] == 50
    assert att["zip_max_decompressed_mb"] == 500
    assert att["zip_max_entries"] == 1000


def test_attachments_enabled_false_survives():
    user = _minimal_user_cfg()
    # NOTE: merged into the existing chat_app stub (not a wholesale
    # `user["services"]["chat_app"] = {...}` replacement) so the auth/alerts
    # stubs _minimal_user_cfg() needs elsewhere in chat_app survive - see the
    # NOTE in _minimal_user_cfg() above.
    user["services"]["chat_app"]["attachments"] = {"enabled": False, "max_file_mb": 5}
    cfg = _render(user)
    att = cfg["services"]["chat_app"]["attachments"]
    assert att["enabled"] is False          # explicit False must NOT be swallowed
    assert att["max_file_mb"] == 5


def test_quota_and_ttl_zero_survives():
    # 0 is the DISABLE sentinel for both knobs, so it must NOT be swallowed and
    # replaced by the default (the template uses default(x, false) for these).
    user = _minimal_user_cfg()
    user["services"]["chat_app"]["attachments"] = {
        "max_total_mb_per_user": 0,
        "abandoned_conversation_ttl_hours": 0,
    }
    att = _render(user)["services"]["chat_app"]["attachments"]
    assert att["max_total_mb_per_user"] == 0
    assert att["abandoned_conversation_ttl_hours"] == 0


def test_attachment_tool_defaults_render():
    cfg = _render(_minimal_user_cfg())
    att = cfg["services"]["chat_app"]["attachments"]
    assert att["agent_tools_enabled"] is True
    assert att["inline_char_limit"] == 32000
    assert att["tool_read_max_chars"] == 20000
    assert att["tool_read_max_bytes"] == 8388608
    assert att["tool_list_max_chars"] == 40000
    assert att["tool_search_max_results"] == 20


def test_agent_tools_enabled_false_survives():
    user = _minimal_user_cfg()
    user["services"]["chat_app"]["attachments"] = {"agent_tools_enabled": False}
    cfg = _render(user)
    assert cfg["services"]["chat_app"]["attachments"]["agent_tools_enabled"] is False
