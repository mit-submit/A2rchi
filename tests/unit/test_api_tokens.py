"""
Unit tests for the multi-token API in UserService.

Covers:
- create_api_token / list_api_tokens / revoke_api_token_by_id / get_user_by_api_token
- Legacy "personal" token shims: generate_api_token / has_api_token / revoke_api_token

The tests mock the psycopg2 connection/cursor so they run without a real DB.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg2.errors
import pytest

from src.utils.user_service import UserService


def _make_cursor():
    """A cursor mock with sensible defaults for rowcount/fetchone."""
    cur = MagicMock()
    cur.rowcount = 1
    cur.fetchone.return_value = None
    return cur


def _wire_conn(cursor):
    """Wrap a cursor mock in a conn mock whose cursor() context manager yields it."""
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cursor)
    ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = ctx
    return conn


@pytest.fixture
def service():
    svc = UserService(pg_config={"host": "localhost"})
    svc._release_connection = MagicMock()
    return svc


def _attach(service, cursor):
    """Install a fresh connection that returns `cursor` from its cursor() ctx."""
    conn = _wire_conn(cursor)
    service._get_connection = MagicMock(return_value=conn)
    return conn


# ---------------------------------------------------------------------------
# create_api_token
# ---------------------------------------------------------------------------

class TestCreateApiToken:

    def _prime(self, cursor, *, user_exists=True, insert_id="token-uuid"):
        """Program the cursor to simulate: user lookup hit, INSERT returning id."""
        cursor.fetchone.side_effect = [
            (1,) if user_exists else None,  # SELECT 1 FROM users
            (insert_id,),                    # RETURNING id
        ]

    def test_returns_token_id_and_plaintext(self, service):
        cur = _make_cursor()
        self._prime(cur, insert_id="abc-123")
        _attach(service, cur)

        result = service.create_api_token("user1", "openwebui")

        assert result["id"] == "abc-123"
        assert result["name"] == "openwebui"
        assert result["token"].startswith("archi_")
        assert len(result["token"]) == 6 + 32  # "archi_" + 32 hex chars

    def test_stores_sha256_hash(self, service):
        cur = _make_cursor()
        self._prime(cur)
        _attach(service, cur)

        result = service.create_api_token("user1", "openwebui")
        expected_hash = hashlib.sha256(result["token"].encode()).hexdigest()

        insert_call = cur.execute.call_args_list[1]
        params = insert_call[0][1]
        assert params == ("user1", "openwebui", expected_hash)

    def test_raises_if_user_not_found(self, service):
        cur = _make_cursor()
        self._prime(cur, user_exists=False)
        _attach(service, cur)

        with pytest.raises(ValueError, match="User not found"):
            service.create_api_token("ghost", "openwebui")

    def test_raises_on_empty_name(self, service):
        cur = _make_cursor()
        _attach(service, cur)

        with pytest.raises(ValueError, match="non-empty"):
            service.create_api_token("user1", "   ")

    def test_strips_name_whitespace(self, service):
        cur = _make_cursor()
        self._prime(cur)
        _attach(service, cur)

        result = service.create_api_token("user1", "  openwebui  ")
        assert result["name"] == "openwebui"

    def test_raises_on_duplicate_name(self, service):
        cur = _make_cursor()
        cur.fetchone.side_effect = [(1,)]  # user exists
        cur.execute.side_effect = [
            None,  # SELECT 1 FROM users
            psycopg2.errors.UniqueViolation("duplicate"),  # INSERT
        ]
        _attach(service, cur)

        with pytest.raises(ValueError, match="already in use"):
            service.create_api_token("user1", "openwebui")

    @patch("src.utils.user_service.log_authentication_event")
    def test_logs_audit_event(self, mock_log, service):
        cur = _make_cursor()
        self._prime(cur)
        _attach(service, cur)

        service.create_api_token("user1", "openwebui")
        mock_log.assert_called_once_with(
            "user1", "api_token_create", success=True, method="bearer_token",
        )


# ---------------------------------------------------------------------------
# list_api_tokens
# ---------------------------------------------------------------------------

class TestListApiTokens:

    def _make_row(self, **overrides):
        row = {
            "id": "token-1",
            "user_id": "user1",
            "user_email": "user1@example.com",
            "name": "openwebui",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "last_used_at": None,
            "revoked_at": None,
        }
        row.update(overrides)
        return row

    def test_returns_serialized_rows(self, service):
        cur = _make_cursor()
        cur.fetchall.return_value = [self._make_row()]
        _attach(service, cur)

        tokens = service.list_api_tokens()

        assert len(tokens) == 1
        t = tokens[0]
        assert t["id"] == "token-1"
        assert t["user_email"] == "user1@example.com"
        assert t["created_at"].startswith("2026-01-01")
        assert t["last_used_at"] is None
        assert t["revoked_at"] is None

    def test_without_user_id_lists_all(self, service):
        cur = _make_cursor()
        cur.fetchall.return_value = []
        _attach(service, cur)

        service.list_api_tokens()

        sql = cur.execute.call_args[0][0]
        assert "WHERE t.user_id" not in sql

    def test_with_user_id_filters(self, service):
        cur = _make_cursor()
        cur.fetchall.return_value = []
        _attach(service, cur)

        service.list_api_tokens(user_id="user1")

        sql, params = cur.execute.call_args[0][0], cur.execute.call_args[0][1]
        assert "WHERE t.user_id" in sql
        assert params == ("user1",)


# ---------------------------------------------------------------------------
# revoke_api_token_by_id
# ---------------------------------------------------------------------------

class TestRevokeApiTokenById:

    def test_revokes_and_returns_true(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = ("user1",)
        _attach(service, cur)

        assert service.revoke_api_token_by_id("token-1") is True

    def test_returns_false_if_not_found(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = None
        _attach(service, cur)

        assert service.revoke_api_token_by_id("token-missing") is False

    def test_user_id_scopes_the_update(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = ("user1",)
        _attach(service, cur)

        service.revoke_api_token_by_id("token-1", user_id="user1")

        sql, params = cur.execute.call_args[0][0], cur.execute.call_args[0][1]
        assert "user_id = %s" in sql
        assert params == ("token-1", "user1")

    @patch("src.utils.user_service.log_authentication_event")
    def test_logs_audit_event_on_success(self, mock_log, service):
        cur = _make_cursor()
        cur.fetchone.return_value = ("user1",)
        _attach(service, cur)

        service.revoke_api_token_by_id("token-1")
        mock_log.assert_called_once_with(
            "user1", "api_token_revoke", success=True, method="bearer_token",
        )


# ---------------------------------------------------------------------------
# get_user_by_api_token
# ---------------------------------------------------------------------------

class TestGetUserByApiToken:

    def _make_row(self, *, token_created_at=None):
        return {
            "id": "user1",
            "display_name": "Test User",
            "email": "test@example.com",
            "auth_provider": "sso",
            "is_admin": True,
            "theme": "system",
            "preferred_model": None,
            "preferred_temperature": None,
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "token_id": "token-1",
            "token_created_at": token_created_at,
        }

    def test_valid_token_returns_user(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = self._make_row()
        _attach(service, cur)

        token = "archi_abc"
        user = service.get_user_by_api_token(token)

        assert user is not None
        assert user.id == "user1"
        assert user.is_admin is True

        select_params = cur.execute.call_args_list[0][0][1]
        assert select_params == (hashlib.sha256(token.encode()).hexdigest(),)

    def test_invalid_token_returns_none(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = None
        _attach(service, cur)

        assert service.get_user_by_api_token("archi_bogus") is None

    def test_bumps_last_used_at_on_hit(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = self._make_row()
        _attach(service, cur)

        service.get_user_by_api_token("archi_abc")

        update_call = cur.execute.call_args_list[1]
        assert "UPDATE api_tokens SET last_used_at" in update_call[0][0]
        assert update_call[0][1] == ("token-1",)

    def test_expired_token_returns_none(self, service):
        cur = _make_cursor()
        old = datetime.now(timezone.utc) - timedelta(days=100)
        cur.fetchone.return_value = self._make_row(token_created_at=old)
        _attach(service, cur)

        assert service.get_user_by_api_token("archi_abc", token_ttl_days=90) is None

    def test_recent_token_within_ttl_returns_user(self, service):
        cur = _make_cursor()
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        cur.fetchone.return_value = self._make_row(token_created_at=recent)
        _attach(service, cur)

        user = service.get_user_by_api_token("archi_abc", token_ttl_days=90)
        assert user is not None

    def test_null_created_at_skips_ttl_check(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = self._make_row(token_created_at=None)
        _attach(service, cur)

        user = service.get_user_by_api_token("archi_abc", token_ttl_days=90)
        assert user is not None


# ---------------------------------------------------------------------------
# Legacy single-token convenience API
# ---------------------------------------------------------------------------

class TestLegacyPersonalToken:

    def test_generate_revokes_existing_then_creates_personal(self, service):
        """generate_api_token should revoke the old personal token then insert a new one."""
        cur = _make_cursor()
        # Sequence: UPDATE revoke (no fetchone), SELECT user exists, INSERT RETURNING id
        cur.fetchone.side_effect = [(1,), ("new-token-id",)]
        _attach(service, cur)

        token = service.generate_api_token("user1")

        assert token.startswith("archi_")
        # First call is the revoke-previous UPDATE; must filter on 'personal'
        first_sql, first_params = cur.execute.call_args_list[0][0]
        assert "UPDATE api_tokens" in first_sql
        assert first_params == ("user1", "personal")

    def test_regeneration_produces_distinct_tokens(self, service):
        tokens = []
        for _ in range(2):
            cur = _make_cursor()
            cur.fetchone.side_effect = [(1,), ("id",)]
            _attach(service, cur)
            tokens.append(service.generate_api_token("user1"))

        assert tokens[0] != tokens[1]

    def test_has_api_token_true_when_row_exists(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = (1,)
        _attach(service, cur)

        assert service.has_api_token("user1") is True
        sql, params = cur.execute.call_args[0][0], cur.execute.call_args[0][1]
        assert "name = %s" in sql
        assert params == ("user1", "personal")

    def test_has_api_token_false_when_missing(self, service):
        cur = _make_cursor()
        cur.fetchone.return_value = None
        _attach(service, cur)

        assert service.has_api_token("user1") is False

    def test_revoke_scoped_to_personal_name(self, service):
        cur = _make_cursor()
        cur.rowcount = 1
        _attach(service, cur)

        assert service.revoke_api_token("user1") is True
        sql, params = cur.execute.call_args[0][0], cur.execute.call_args[0][1]
        assert "name = %s" in sql
        assert params == ("user1", "personal")

    def test_revoke_returns_false_when_nothing_revoked(self, service):
        cur = _make_cursor()
        cur.rowcount = 0
        _attach(service, cur)

        assert service.revoke_api_token("user1") is False

    @patch("src.utils.user_service.log_authentication_event")
    def test_revoke_logs_audit_on_success(self, mock_log, service):
        cur = _make_cursor()
        cur.rowcount = 1
        _attach(service, cur)

        service.revoke_api_token("user1")
        mock_log.assert_called_once_with(
            "user1", "api_token_revoke", success=True, method="bearer_token",
        )
