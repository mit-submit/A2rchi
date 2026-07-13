"""Unit tests for the playbooks REST Blueprint (playbook_routes.py).

The Blueprint depends only on flask + PlaybookService types, so these run
wherever flask is installed (CI, the chat image, or a dev env with the pinned
flask) — no LLM stack needed. Identity resolution and storage are injected as
fakes; what is under test is the route-layer contract: status codes, input
hardening, and that per-app wiring reaches the right service.
"""
import pytest

flask = pytest.importorskip("flask", reason="blueprint tests need flask")

from unittest.mock import MagicMock

from src.interfaces.chat_app.playbook_routes import register_playbooks


def _passthrough_auth(view):
    return view


def _make_app(owner="anon-1", svc=None, auth_enabled=False, resolve_owner=None):
    app = flask.Flask(__name__)
    register_playbooks(
        app,
        auth_enabled=auth_enabled,
        require_auth=_passthrough_auth,
        resolve_owner=resolve_owner or (lambda cid: (owner, None)),
        playbook_svc=lambda: svc if svc is not None else MagicMock(),
    )
    return app


# ── R1-pattern hardening on the opt-in endpoints ─────────────────────────────

def test_enable_with_non_dict_json_body_is_not_500():
    """A truthy non-dict JSON body ([1]) must not crash .get() into a 500 —
    the guard the sibling create/update/delete/import handlers already carry."""
    svc = MagicMock()
    client = _make_app(svc=svc).test_client()

    resp = client.post("/api/playbooks/1/enable?client_id=c1", json=[1])

    assert resp.status_code != 500
    svc.enable_playbook.assert_called_once_with("anon-1", 1)


def test_disable_with_non_dict_json_body_is_not_500():
    svc = MagicMock()
    client = _make_app(svc=svc).test_client()

    resp = client.post("/api/playbooks/1/disable?client_id=c1", json="just a string")

    assert resp.status_code != 500
    svc.disable_playbook.assert_called_once_with("anon-1", 1)


def test_enable_happy_path_uses_body_client_id():
    svc = MagicMock()
    seen = []

    def resolve(cid):
        seen.append(cid)
        return "owner-from-" + str(cid), None

    client = _make_app(svc=svc, resolve_owner=resolve).test_client()

    resp = client.post("/api/playbooks/9/enable", json={"client_id": "c9"})

    assert resp.status_code == 200
    assert seen == ["c9"]
    svc.enable_playbook.assert_called_once_with("owner-from-c9", 9)


# ── Listing must not fetch bodies it immediately discards ────────────────────

def test_list_route_skips_bodies():
    """GET /api/playbooks never serializes bodies (its docstring says
    '(no bodies)') — so don't fetch up to 16KB x (own + public) of them per
    Settings open just to throw them away."""
    svc = MagicMock()
    svc.list_playbooks.return_value = []
    svc.list_enabled_playbook_ids.return_value = set()
    client = _make_app(svc=svc).test_client()

    resp = client.get("/api/playbooks?client_id=c1")

    assert resp.status_code == 200
    svc.list_playbooks.assert_called_once_with("anon-1", with_bodies=False)


# ── Per-app wiring: a second app must not repoint (or break) the first ───────

def test_two_apps_keep_their_own_wiring():
    """Registering a second FlaskAppWrapper in the same process must neither
    crash (blueprint setup methods cannot be re-run after first registration)
    nor repoint the first app's routes at the second app's identity/service."""
    svc_a, svc_b = MagicMock(), MagicMock()
    app_a = _make_app(owner="owner-a", svc=svc_a)
    app_b = _make_app(owner="owner-b", svc=svc_b)

    resp_a = app_a.test_client().post("/api/playbooks/1/enable?client_id=x", json={})
    resp_b = app_b.test_client().post("/api/playbooks/2/enable?client_id=y", json={})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    svc_a.enable_playbook.assert_called_once_with("owner-a", 1)
    svc_b.enable_playbook.assert_called_once_with("owner-b", 2)


def test_auth_gate_applies_per_app():
    """The before_request probe must use THIS app's require_auth."""
    def deny_auth(_view):
        def _denied(*_a, **_k):
            return flask.jsonify({"error": "denied"}), 401
        return _denied

    app = flask.Flask(__name__)
    register_playbooks(
        app,
        auth_enabled=True,
        require_auth=deny_auth,
        resolve_owner=lambda cid: ("o", None),
        playbook_svc=lambda: MagicMock(),
    )
    open_app = _make_app()  # a second, passthrough-auth app stays open

    assert app.test_client().get("/api/playbooks?client_id=c").status_code == 401
    assert open_app.test_client().get("/api/playbooks?client_id=c").status_code == 200
