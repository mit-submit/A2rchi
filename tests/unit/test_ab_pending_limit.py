from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

from src.interfaces.chat_app.app import FlaskAppWrapper
from src.utils.conversation_service import ABComparison


def _build_wrapper(*, sample_rate=1.0, max_pending=1, pending_count=0):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.chat = Mock()
    wrapper.chat.ab_pool = SimpleNamespace(
        enabled=True,
        sample_rate=sample_rate,
        comparison_rate=sample_rate,
        disclosure_mode="post_vote_reveal",
        default_trace_mode="hidden",
        max_pending_per_conversation=max_pending,
        variant_label_mode="post_vote_reveal",
        activity_panel_default_state="hidden",
        max_pending_comparisons_per_conversation=max_pending,
    )
    wrapper.chat.conv_service = Mock()
    wrapper.chat.conv_service.count_pending_ab_comparisons.return_value = pending_count
    wrapper.chat.conv_service.get_pending_ab_comparison.return_value = ABComparison(comparison_id=91)
    wrapper.chat.query_conversation_history = Mock()
    wrapper._get_ab_participation_state = Mock(return_value={
        "can_participate": True,
        "eligible": True,
        "reason": "eligible",
        "targeted": True,
    })
    wrapper._get_effective_ab_sample_rate = Mock(return_value=sample_rate)
    return app, wrapper


def test_ab_decision_allows_second_unresolved_comparison_below_limit():
    app, wrapper = _build_wrapper(sample_rate=1.0, max_pending=2, pending_count=1)

    with app.test_request_context("/api/ab/decision?client_id=tester&conversation_id=8"):
        response, status = FlaskAppWrapper.ab_get_decision(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["use_ab"] is True
    assert payload["reason"] == "sampled"
    assert payload["max_pending_comparisons_per_conversation"] == 2


def test_ab_decision_blocks_when_pending_limit_is_reached():
    app, wrapper = _build_wrapper(sample_rate=1.0, max_pending=2, pending_count=2)

    with app.test_request_context("/api/ab/decision?client_id=tester&conversation_id=8"):
        response, status = FlaskAppWrapper.ab_get_decision(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["use_ab"] is False
    assert payload["reason"] == "pending_vote"
    assert payload["pending_count"] == 2
    assert payload["max_pending_comparisons_per_conversation"] == 2


def test_ab_pending_endpoint_returns_all_pending_comparisons():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.chat = Mock()
    wrapper.chat.conv_service = Mock()
    wrapper.chat.query_conversation_history = Mock()

    comparisons = [
        ABComparison(comparison_id=10),
        ABComparison(comparison_id=11),
    ]
    wrapper.chat.conv_service.get_pending_ab_comparisons.return_value = comparisons
    wrapper._serialize_pending_ab_comparisons = Mock(
        return_value=[
            {"comparison_id": 10},
            {"comparison_id": 11},
        ]
    )

    with app.test_request_context("/api/ab/pending?client_id=tester&conversation_id=8"):
        response, status = FlaskAppWrapper.ab_get_pending(wrapper)

    payload = response.get_json()
    assert status == 200
    assert payload["pending_count"] == 2
    assert payload["comparison"] == {"comparison_id": 11}
    assert payload["comparisons"] == [{"comparison_id": 10}, {"comparison_id": 11}]
