import pytest
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.archi.utils.output_dataclass import PipelineOutput
from src.cli.managers.config_manager import ConfigurationManager
from src.interfaces.chat_app.app import ChatWrapper
from src.utils.ab_testing import ABPool, ABPoolError, load_ab_pool_state


def test_ab_pool_from_config_requires_label_and_agent_spec():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md", "model": "gpt-4o"},
                ],
            },
        }
    )

    assert pool.champion_name == "Baseline"
    assert pool.pool_info()["champion"] == "Baseline"
    assert [variant.label for variant in pool.variants] == ["Baseline", "Challenger"]
    assert [variant.agent_spec for variant in pool.variants] == ["baseline.md", "challenger.md"]
    assert pool.pool_info()["variants"] == ["Baseline", "Challenger"]
    assert pool.pool_info()["variant_details"] == [
        {"label": "Baseline", "agent_spec": "baseline.md"},
        {"label": "Challenger", "agent_spec": "challenger.md", "model": "gpt-4o"},
    ]


def test_ab_pool_from_config_rejects_duplicate_labels():
    with pytest.raises(ABPoolError, match="Duplicate variant label"):
        ABPool.from_config(
            {
                "enabled": True,
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"label": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Baseline", "agent_spec": "challenger.md"},
                    ],
                },
            }
        )


def test_ab_pool_from_config_rejects_missing_agent_spec():
    with pytest.raises(ABPoolError, match="agent_spec"):
        ABPool.from_config(
            {
                "enabled": True,
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"label": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Challenger"},
                    ],
                },
            }
        )


def test_ab_pool_from_config_rejects_name_only_variant_config():
    with pytest.raises(ABPoolError, match="deprecated 'name'"):
        ABPool.from_config(
            {
                "enabled": True,
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"name": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Challenger", "agent_spec": "challenger.md"},
                    ],
                },
            }
        )


def test_load_ab_pool_state_allows_incomplete_setup_with_warning():
    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                    }
                }
            }
        }
    )

    assert state.pool is None
    assert state.enabled_requested is True
    assert state.warnings
    assert "inactive" in state.warnings[-1].lower()


def test_load_ab_pool_state_requires_ab_agent_specs_to_exist(tmp_path):
    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "ab_agents_dir": str(tmp_path),
                        "pool": {
                            "champion": "Baseline",
                            "variants": [
                                {"label": "Baseline", "agent_spec": "baseline.md"},
                                {"label": "Challenger", "agent_spec": "challenger.md"},
                            ],
                        },
                    }
                }
            }
        }
    )

    assert state.pool is None
    assert "missing" in state.warnings[-1].lower()


def test_load_ab_pool_state_activates_when_ab_specs_exist(tmp_path):
    (tmp_path / "baseline.md").write_text("---\nname: Baseline\ntools:\n  - search\n---\nBaseline prompt\n")
    (tmp_path / "challenger.md").write_text("---\nname: Challenger\ntools:\n  - search\n---\nChallenger prompt\n")

    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "ab_agents_dir": str(tmp_path),
                        "pool": {
                            "champion": "Baseline",
                            "variants": [
                                {"label": "Baseline", "agent_spec": "baseline.md"},
                                {"label": "Challenger", "agent_spec": "challenger.md"},
                            ],
                        },
                    }
                }
            }
        }
    )

    assert state.pool is not None
    assert state.pool.champion_name == "Baseline"


def test_load_ab_pool_state_accepts_database_spec_lookup_callback():
    state = load_ab_pool_state(
        {
            "services": {
                "chat_app": {
                    "ab_testing": {
                        "enabled": True,
                        "pool": {
                            "champion": "Baseline",
                            "variants": [
                                {"label": "Baseline", "agent_spec": "baseline.md"},
                                {"label": "Challenger", "agent_spec": "challenger.md"},
                            ],
                        },
                    }
                }
            }
        },
        agent_spec_exists=lambda filename: filename in {"baseline.md", "challenger.md"},
    )

    assert state.pool is not None
    assert state.pool.champion_name == "Baseline"


def test_ab_pool_targeting_requires_role_and_permission_groups_when_both_are_set():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "eligible_roles": ["archi-expert"],
            "eligible_permissions": ["ab:metrics"],
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md"},
                ],
            },
        }
    )

    assert pool.is_targeted_user(
        roles=["archi-expert"],
        permissions=["ab:metrics"],
    ) is True
    assert pool.is_targeted_user(
        roles=["archi-expert"],
        permissions=["chat:query"],
    ) is False
    assert pool.is_targeted_user(
        roles=["base-user"],
        permissions=["ab:metrics"],
    ) is False


def test_ab_pool_targeting_matches_any_role_or_permission_within_each_group():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "eligible_roles": ["archi-expert", "reviewer"],
            "eligible_permissions": ["ab:view", "ab:metrics"],
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md"},
                ],
            },
        }
    )

    assert pool.is_targeted_user(
        roles=["reviewer"],
        permissions=["ab:view"],
    ) is True
    assert pool.is_targeted_user(
        roles=["archi-expert"],
        permissions=["ab:metrics"],
    ) is True


def test_ab_pool_from_config_accepts_canonical_variant_label_and_activity_values():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "variant_label_mode": "always_visible",
            "activity_panel_default_state": "collapsed",
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md"},
                ],
            },
        }
    )

    assert pool.disclosure_mode == "always_visible"
    assert pool.default_trace_mode == "collapsed"


def test_ab_pool_from_config_maps_previous_disclosure_names_to_canonical_values():
    pool = ABPool.from_config(
        {
            "enabled": True,
            "disclosure_mode": "reveal_after_vote",
            "pool": {
                "champion": "Baseline",
                "variants": [
                    {"label": "Baseline", "agent_spec": "baseline.md"},
                    {"label": "Challenger", "agent_spec": "challenger.md"},
                ],
            },
        }
    )

    assert pool.disclosure_mode == "post_vote_reveal"


def test_ab_pool_from_config_rejects_legacy_trace_values():
    with pytest.raises(ABPoolError, match="activity_panel_default_state"):
        ABPool.from_config(
            {
                "enabled": True,
                "activity_panel_default_state": "minimal",
                "pool": {
                    "champion": "Baseline",
                    "variants": [
                        {"label": "Baseline", "agent_spec": "baseline.md"},
                        {"label": "Challenger", "agent_spec": "challenger.md"},
                    ],
                },
            }
        )


def test_validate_ab_testing_config_allows_incomplete_ui_bootstrap():
    manager = object.__new__(ConfigurationManager)

    manager._validate_ab_testing_config(
        {
            "ab_testing": {
                "enabled": True,
                "comparison_rate": 0.2,
            }
        }
    )


def test_chat_refresh_ab_pool_merges_import_warnings_with_pool_state():
    chat = object.__new__(ChatWrapper)
    chat.config = {
        "services": {
            "chat_app": {
                "ab_testing": {
                    "enabled": True,
                    "pool": {
                        "champion": "Baseline",
                        "variants": [
                            {"label": "Baseline", "agent_spec": "baseline.md"},
                            {"label": "Challenger", "agent_spec": "challenger.md"},
                        ],
                    },
                }
            }
        }
    }
    chat.ab_agent_spec_service = Mock()
    chat.ab_agent_spec_service.spec_exists = Mock(return_value=True)
    chat._sync_ab_agent_specs_from_filesystem = Mock(return_value={
        "warnings": ["A/B agent import conflict: baseline.md failed to import"],
        "conflicts": ["baseline.md failed to import"],
        "imported": 0,
        "updated": 0,
        "skipped": 0,
    })

    with patch(
        "src.interfaces.chat_app.app.load_ab_pool_state",
        return_value=SimpleNamespace(
            pool=None,
            warnings=["A/B testing is enabled but inactive because the A/B agent pool is missing: ['baseline.md']."],
            enabled_requested=True,
            agent_dir="/root/archi/ab_agents",
            agent_dir_configured=True,
        ),
    ):
        ChatWrapper.refresh_ab_pool(chat)

    assert chat.ab_pool is None
    assert chat.ab_pool_state.warnings == [
        "A/B agent import conflict: baseline.md failed to import",
        "A/B testing is enabled but inactive because the A/B agent pool is missing: ['baseline.md'].",
    ]
    assert chat.ab_agent_import_diagnostics["conflicts"] == ["baseline.md failed to import"]


def test_stream_ab_comparison_emits_per_arm_final_before_ab_meta():
    def make_text_output(text):
        return PipelineOutput(
            answer=text,
            metadata={"event_type": "text"},
            final=False,
        )

    def make_final_output(text, model_name, prompt_tokens):
        return PipelineOutput(
            answer=text,
            metadata={
                "event_type": "final",
                "model": model_name,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 4,
                    "total_tokens": prompt_tokens + 4,
                },
            },
            final=True,
        )

    def stream_fast_arm(**_kwargs):
        yield make_text_output("Fast arm partial")
        yield make_final_output("Fast arm done", "gpt-4o", 7)

    def stream_slow_arm(**_kwargs):
        yield make_text_output("Slow arm partial")
        time.sleep(0.05)
        yield make_final_output("Slow arm done", "claude-3.5-sonnet", 9)

    fast_variant = SimpleNamespace(
        name="Baseline",
        provider="openai",
        model="gpt-4o",
        to_meta_json=Mock(return_value='{"label":"Baseline"}'),
    )
    slow_variant = SimpleNamespace(
        name="Poet",
        provider="anthropic",
        model="claude-3.5-sonnet",
        to_meta_json=Mock(return_value='{"label":"Poet"}'),
    )

    chat = object.__new__(ChatWrapper)
    chat.ab_pool = Mock()
    chat.ab_pool.sample_matchup.return_value = (fast_variant, slow_variant, True)
    chat.ab_pool.variant_label_mode = "post_vote_reveal"
    chat.archi = SimpleNamespace(vs_connector=SimpleNamespace(get_vectorstore=Mock(return_value=object())))
    chat.update_config = Mock()
    chat._resolve_config_name = Mock(return_value="default")
    chat._prepare_chat_context = Mock(return_value=(
        SimpleNamespace(
            history=["hello"],
            conversation_id=123,
            sender="user",
            content="Hello",
        ),
        None,
    ))
    chat._resolve_runtime_ab_variant = Mock(side_effect=lambda variant: (variant, None))
    chat._create_variant_archi = Mock(side_effect=[
        SimpleNamespace(pipeline=SimpleNamespace(stream=stream_fast_arm)),
        SimpleNamespace(pipeline=SimpleNamespace(stream=stream_slow_arm)),
    ])
    chat._message_content = Mock(side_effect=lambda message: getattr(message, "content", ""))
    chat._store_assistant_message = Mock(side_effect=[101, 102])
    chat._get_last_user_message_id = Mock(return_value=100)
    chat.conv_service = Mock()
    chat.conv_service.create_ab_comparison.return_value = 42
    chat.current_pipeline_used = "react"

    events = list(
        chat.stream_ab_comparison(
            message=["Hello"],
            conversation_id="123",
            client_id="client-1",
            is_refresh=True,
            server_received_msg_ts=0,
            client_sent_msg_ts=0.0,
            client_timeout=60.0,
            config_name="default",
        )
    )

    final_a = next(event for event in events if event.get("type") == "final" and event.get("arm") == "a")
    final_b = next(event for event in events if event.get("type") == "final" and event.get("arm") == "b")
    ab_meta = next(event for event in events if event.get("type") == "ab_meta")

    assert final_a["response"] == "Fast arm done"
    assert final_a["model"] == "gpt-4o"
    assert final_a["model_used"] == "openai/gpt-4o"
    assert final_a["usage"]["prompt_tokens"] == 7
    assert final_a["duration_ms"] >= 0

    assert final_b["response"] == "Slow arm done"
    assert final_b["model"] == "claude-3.5-sonnet"
    assert final_b["model_used"] == "anthropic/claude-3.5-sonnet"
    assert final_b["usage"]["prompt_tokens"] == 9
    assert final_b["duration_ms"] >= final_a["duration_ms"]

    assert events.index(final_a) < events.index(final_b)
    assert events.index(final_b) < events.index(ab_meta)


def test_chat_source_footer_uses_retrieved_documents_label():
    footer = ChatWrapper.format_links_markdown([
        {"display": "Doc 1", "link": "https://example.com/doc-1", "score": 0.1},
    ])

    assert "Retrieved documents (1)" in footer
    assert ChatWrapper.AUTO_SOURCE_SECTION_EXPLANATION in footer
    assert "Show all sources" not in footer


def test_chat_source_footer_is_not_appended_when_answer_already_contains_source_section():
    answer_with_existing_section = (
        "Answer body\n\n"
        "<details><summary><strong>Show all sources (34)</strong></summary>\n"
        "- Existing source\n"
        "</details>\n"
    )

    output = ChatWrapper.append_source_section(
        answer_with_existing_section,
        [{"display": "Doc 1", "link": "https://example.com/doc-1", "score": 0.1}],
        render_markdown=False,
    )

    assert output == answer_with_existing_section
    assert output.count("Show all sources (34)") == 1
    assert "Retrieved documents (1)" not in output
