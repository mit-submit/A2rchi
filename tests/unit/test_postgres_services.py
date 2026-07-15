"""
Unit tests for PostgreSQL services.

Tests cover:
- ConnectionPool
- UserService  
- ConfigService
- DocumentSelectionService
- ConversationService
- PostgresServiceFactory
"""
import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

# Import services
from src.utils.connection_pool import ConnectionPool, ConnectionPoolError, ConnectionTimeoutError
from src.utils.user_service import UserService, User
from src.utils.config_service import ConfigService, StaticConfig, DynamicConfig, ConfigValidationError
from src.utils.document_selection_service import DocumentSelectionService, DocumentSelection
from src.utils.conversation_service import ConversationService, Message, ABComparison
from src.utils.postgres_service_factory import PostgresServiceFactory, create_services
from src.utils.playbook_service import (
    PlaybookService, Playbook,
    PlaybookValidationError, PlaybookConflictError, PlaybookNotFoundError,
    resolve_playbook_owner,
)
from psycopg2 import errors as pg_errors


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_connection():
    """Create a mock psycopg2 connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


@pytest.fixture
def mock_pool(mock_connection):
    """Create a mock connection pool."""
    conn, cursor = mock_connection
    pool = MagicMock(spec=ConnectionPool)
    pool.get_connection.return_value = conn
    pool.get_connection_direct.return_value = conn
    pool.release_connection = MagicMock()
    return pool


# =============================================================================
# ConnectionPool Tests
# =============================================================================

class TestConnectionPool:
    """Tests for ConnectionPool."""
    
    def test_init_requires_params_or_dsn(self):
        """Test that ConnectionPool requires connection info."""
        with pytest.raises(ValueError, match="Either pg_config or connection_params must be provided"):
            ConnectionPool()
    
    @patch('psycopg2.pool.ThreadedConnectionPool')
    def test_init_with_params(self, mock_tcp):
        """Test initialization with connection params."""
        params = {
            'host': 'localhost',
            'port': 5432,
            'database': 'test',
            'user': 'user',
            'password': 'pass',
        }
        pool = ConnectionPool(connection_params=params)
        
        mock_tcp.assert_called_once()
        assert pool._pool is not None
    
    @patch('psycopg2.pool.ThreadedConnectionPool')
    def test_singleton_pattern(self, mock_tcp):
        """Test singleton pattern."""
        params = {'host': 'localhost', 'database': 'test', 'user': 'user', 'password': 'pass'}
        
        # Reset singleton
        ConnectionPool._instance = None
        
        pool1 = ConnectionPool.get_instance(connection_params=params)
        pool2 = ConnectionPool.get_instance()
        
        assert pool1 is pool2


# =============================================================================
# UserService Tests
# =============================================================================

class TestUserService:
    """Tests for UserService."""
    
    def test_get_or_create_user_creates_new(self, mock_pool, mock_connection):
        """Test creating a new user."""
        conn, cursor = mock_connection
        # First call (get_user check) returns None, second call (INSERT) returns dict
        cursor.fetchone.side_effect = [
            None,  # User doesn't exist on initial check
            {  # INSERT RETURNING result
                "id": "user123",
                "display_name": None,
                "email": None,
                "auth_provider": "anonymous",
                "theme": "system",
                "preferred_model": None,
                "preferred_temperature": None,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }
        ]
        
        service = UserService(connection_pool=mock_pool, encryption_key="test-key")
        user = service.get_or_create_user("user123", auth_provider="anonymous")
        
        assert user.id == "user123"
        assert user.auth_provider == "anonymous"

    def test_record_login_increments_count_and_stamps_time(self, mock_pool, mock_connection):
        """record_login must bump login_count and set last_login_at=NOW() for the user — the SSO
        callback upserts the user but never touched these, so logins were untracked (count stayed
        0, last_login_at NULL)."""
        conn, cursor = mock_connection
        service = UserService(connection_pool=mock_pool, encryption_key="test-key")
        # Ignore any setup (e.g. schema-ensure) the constructor ran on the shared mock conn.
        conn.reset_mock()
        cursor.reset_mock()

        service.record_login("vkhlaisu")

        sql, params = cursor.execute.call_args[0]
        assert "UPDATE users" in sql
        assert "login_count = login_count + 1" in sql
        assert "last_login_at = NOW()" in sql
        assert params == ("vkhlaisu",)
        conn.commit.assert_called_once()

    def test_get_or_create_user_returns_existing(self, mock_pool, mock_connection):
        """Test returning existing user."""
        conn, cursor = mock_connection
        # Simulate existing user as dict
        cursor.fetchone.return_value = {
            "id": "user123",
            "display_name": "Test User",
            "email": "test@example.com",
            "auth_provider": "basic",
            "theme": "dark",
            "preferred_model": "gpt-4",
            "preferred_temperature": 0.7,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        
        service = UserService(connection_pool=mock_pool, encryption_key="test-key")
        user = service.get_or_create_user("user123")
        
        assert user.id == "user123"
        assert user.display_name == "Test User"
        assert user.theme == "dark"
    
    def test_update_preferences(self, mock_pool, mock_connection):
        """Test updating user preferences."""
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {
            "id": "user123",
            "display_name": "Test User",
            "email": None,
            "auth_provider": "anonymous",
            "theme": "light",
            "preferred_model": "gpt-4o",
            "preferred_temperature": 0.5,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        
        service = UserService(connection_pool=mock_pool, encryption_key="test-key")
        user = service.update_preferences(
            user_id="user123",
            theme="light",
        )
        
        assert user.theme == "light"


# =============================================================================
# ConfigService Tests
# =============================================================================

class TestConfigService:
    """Tests for ConfigService."""
    
    def test_get_static_config(self, mock_pool, mock_connection):
        """Test getting static config."""
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {
            "deployment_name": "test-deployment",
            "config_version": "2.0.0",
            "data_path": "/data",
            "prompts_path": "/prompts",
            "embedding_model": "text-embedding-ada-002",
            "embedding_dimensions": 1536,
            "chunk_size": 1000,
            "chunk_overlap": 150,
            "distance_metric": "cosine",
            "available_pipelines": ["QAPipeline"],
            "available_models": ["gpt-4"],
            "available_providers": ["openai"],
            "auth_enabled": False,
            "session_lifetime_days": 30,
            "created_at": datetime.now(),
        }
        
        service = ConfigService(connection_pool=mock_pool)
        config = service.get_static_config()
        
        assert config.deployment_name == "test-deployment"
        assert config.embedding_dimensions == 1536
        assert "QAPipeline" in config.available_pipelines
    
    def test_get_static_config_caching(self, mock_pool, mock_connection):
        """Test that static config is cached."""
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {
            "deployment_name": "test",
            "config_version": "2.0.0",
            "data_path": "/data",
            "prompts_path": "/prompts",
            "embedding_model": "model",
            "embedding_dimensions": 384,
            "chunk_size": 1000,
            "chunk_overlap": 150,
            "distance_metric": "cosine",
            "available_pipelines": [],
            "available_models": [],
            "available_providers": [],
            "auth_enabled": False,
            "session_lifetime_days": 30,
            "created_at": datetime.now(),
        }
        
        service = ConfigService(connection_pool=mock_pool)
        
        # First call
        config1 = service.get_static_config()
        calls_after_first = mock_pool.get_connection_direct.call_count
        
        # Second call should use cache
        config2 = service.get_static_config()
        calls_after_second = mock_pool.get_connection_direct.call_count
        
        assert config1 is config2
        # Second call should not make additional DB calls (cached)
        assert calls_after_second == calls_after_first
    
    def test_update_dynamic_config_validation(self, mock_pool, mock_connection):
        """Test dynamic config validation."""
        conn, cursor = mock_connection
        
        service = ConfigService(connection_pool=mock_pool)
        
        with pytest.raises(ConfigValidationError, match="temperature"):
            service.update_dynamic_config(temperature=5.0)
        
        with pytest.raises(ConfigValidationError, match="max_tokens"):
            service.update_dynamic_config(max_tokens=-1)


# =============================================================================
# DocumentSelectionService Tests
# =============================================================================

class TestDocumentSelectionService:
    """Tests for DocumentSelectionService."""
    
    def test_get_enabled_document_ids(self, mock_pool, mock_connection):
        """Test getting enabled document IDs."""
        conn, cursor = mock_connection
        cursor.fetchall.return_value = [(1,), (2,), (5,)]
        
        service = DocumentSelectionService(connection_pool=mock_pool)
        doc_ids = service.get_enabled_document_ids(
            user_id="user123",
            conversation_id="conv42",
        )
        
        # Returns a set of IDs
        assert doc_ids == {1, 2, 5}
    
    def test_set_user_default(self, mock_pool, mock_connection):
        """Test setting user default."""
        conn, cursor = mock_connection
        
        service = DocumentSelectionService(connection_pool=mock_pool)
        service.set_user_default(
            user_id="user123",
            document_id=10,
            enabled=False,
        )
        
        # Verify UPSERT was called
        conn.commit.assert_called()
    
    def test_3tier_precedence_query(self, mock_pool, mock_connection):
        """Test that the 3-tier precedence is in the query."""
        conn, cursor = mock_connection
        cursor.fetchall.return_value = []
        
        service = DocumentSelectionService(connection_pool=mock_pool)
        service.get_enabled_document_ids("user", 1)
        
        # Check that the query includes COALESCE for precedence
        call_args = cursor.execute.call_args
        query = call_args[0][0]
        assert "COALESCE" in query


# =============================================================================
# ConversationService Tests
# =============================================================================

class TestConversationService:
    """Tests for ConversationService."""
    
    def test_insert_message(self, mock_pool, mock_connection):
        """Test inserting a message."""
        conn, cursor = mock_connection
        
        # Mock execute_values return
        with patch('src.utils.conversation_service.execute_values') as mock_exec:
            mock_exec.return_value = None
            cursor.fetchall.return_value = [(1,)]
            mock_exec.return_value = [(1,)]
            
            service = ConversationService(connection_pool=mock_pool)
            
            msg = Message(
                conversation_id="conv123",
                sender="user",
                content="Hello",
                model_used="gpt-4",
                pipeline_used="QAPipeline",
            )
            
            # The service calls execute_values which returns IDs
            with patch.object(service, 'insert_messages', return_value=[1]):
                msg_id = service.insert_message(msg)
                assert msg_id == 1
    
    def test_create_ab_comparison(self, mock_pool, mock_connection):
        """Test creating A/B comparison."""
        conn, cursor = mock_connection
        cursor.fetchone.return_value = (42,)  # comparison_id
        
        service = ConversationService(connection_pool=mock_pool)
        comparison_id = service.create_ab_comparison(
            conversation_id="conv123",
            user_prompt_mid=1,
            response_a_mid=2,
            response_b_mid=3,
            model_a="gpt-4",
            pipeline_a="QAPipeline",
            model_b="claude-3",
            pipeline_b="QAPipeline",
        )
        
        assert comparison_id == 42
    
    def test_record_ab_preference_validation(self, mock_pool, mock_connection):
        """Test preference validation."""
        service = ConversationService(connection_pool=mock_pool)
        
        with pytest.raises(ValueError, match="Invalid preference"):
            service.record_ab_preference(1, "invalid")


# =============================================================================
# PostgresServiceFactory Tests
# =============================================================================

class TestPostgresServiceFactory:
    """Tests for PostgresServiceFactory."""
    
    @patch('src.utils.postgres_service_factory.ConnectionPool')
    def test_from_config(self, mock_pool_class):
        """Test factory creation from config."""
        factory = PostgresServiceFactory.from_config(
            connection_params={
                'host': 'localhost',
                'database': 'test',
                'user': 'user',
                'password': 'pass',
            }
        )
        
        assert factory is not None
        mock_pool_class.assert_called_once()
    
    @patch('src.utils.postgres_service_factory.ConnectionPool')
    def test_lazy_service_initialization(self, mock_pool_class):
        """Test that services are lazy-initialized."""
        mock_pool = MagicMock()
        mock_pool_class.return_value = mock_pool
        
        factory = PostgresServiceFactory.from_config(
            connection_params={'host': 'localhost', 'database': 'test', 'user': 'u', 'password': 'p'}
        )
        
        # Services should not be created yet
        assert factory._user_service is None
        assert factory._config_service is None
        
        # Access services
        _ = factory.user_service
        _ = factory.config_service
        
        # Now they should exist
        assert factory._user_service is not None
        assert factory._config_service is not None
    
    @patch('src.utils.postgres_service_factory.ConnectionPool')
    def test_context_manager(self, mock_pool_class):
        """Test context manager cleanup."""
        mock_pool = MagicMock()
        mock_pool_class.return_value = mock_pool
        
        with PostgresServiceFactory.from_config(
            connection_params={'host': 'localhost', 'database': 'test', 'user': 'u', 'password': 'p'}
        ) as factory:
            _ = factory.user_service
        
        # Pool should be closed
        mock_pool.close.assert_called_once()
    
    def test_from_yaml_config_deprecated(self):
        """from_yaml_config should still parse postgres settings for ingest."""
        config = {
            'database': {
                'postgres': {
                    'host': 'db.example.com',
                    'port': 5433,
                    'database': 'archi',
                    'user': 'app',
                    'password': 'secret',
                    'pool': {
                        'min_connections': 2,
                        'max_connections': 10,
                    }
                }
            }
        }

        with patch('src.utils.postgres_service_factory.ConnectionPool') as mock_pool_class:
            factory = PostgresServiceFactory.from_yaml_config(config)

            # Verify connection params were extracted correctly
            call_kwargs = mock_pool_class.call_args[1]
            assert call_kwargs['connection_params']['host'] == 'db.example.com'
            assert call_kwargs['connection_params']['port'] == 5433
            assert call_kwargs['min_conn'] == 2
            assert call_kwargs['max_conn'] == 10

    def test_playbook_service_lazy_init(self, mock_pool):
        factory = PostgresServiceFactory(connection_pool=mock_pool)
        assert factory._playbook_service is None
        svc = factory.playbook_service
        assert isinstance(svc, PlaybookService)
        assert factory._playbook_service is svc  # cached


# =============================================================================
# Integration-style Tests (with mocked DB)
# =============================================================================

class TestServiceIntegration:
    """Integration tests for service interactions."""
    
    @patch('src.utils.postgres_service_factory.ConnectionPool')
    def test_user_document_selection_flow(self, mock_pool_class):
        """Test user setting document defaults."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool_class.return_value = mock_pool
        
        factory = PostgresServiceFactory.from_config(
            connection_params={'host': 'localhost', 'database': 'test', 'user': 'u', 'password': 'p'}
        )
        
        # Set user document default
        factory.document_selection_service.set_user_default(
            user_id="user123",
            document_id=10,
            enabled=False,
        )
        
        # Verify commit was called
        mock_conn.commit.assert_called()


# =============================================================================
# Message/ABComparison Dataclass Tests
# =============================================================================

class TestDataclasses:
    """Tests for dataclass structures."""
    
    def test_message_defaults(self):
        """Test Message default values."""
        msg = Message()
        
        assert msg.message_id is None
        assert msg.conversation_id == ""
        assert msg.sender == ""
        assert msg.archi_service == "chat"
    
    def test_ab_comparison_defaults(self):
        """Test ABComparison default values."""
        ab = ABComparison()
        
        assert ab.comparison_id is None
        assert ab.is_config_a_first is True
        assert ab.preference is None
    
    def test_document_selection_repr(self):
        """Test DocumentSelection representation."""
        ds = DocumentSelection(
            document_id=1,
            resource_hash="abc123",
            display_name="Test Doc",
            source_type="file",
            user_default=False,
            conversation_override=True,
        )
        
        assert ds.document_id == 1
        assert ds.enabled is True  # conversation_override takes precedence


class TestPlaybookService:
    def test_playbook_dataclass_defaults(self):
        playbook = Playbook(id=1, name="rucio-triage", description="d", body="b", owner_id="c1")
        assert playbook.created_at is None
        assert playbook.updated_at is None

    def test_validate_rejects_bad_name(self):
        service = PlaybookService(connection_pool=MagicMock())
        with pytest.raises(PlaybookValidationError, match="lowercase"):
            service.create_playbook("c1", "Bad Name!", "desc", "body")

    def test_validate_rejects_consecutive_hyphens(self):
        # Agent Skills spec: no leading/trailing/consecutive hyphens.
        service = PlaybookService(connection_pool=MagicMock())
        for bad in ("a--b", "-ab", "ab-"):
            with pytest.raises(PlaybookValidationError, match="hyphens"):
                service.create_playbook("c1", bad, "desc", "body")

    def test_validate_rejects_empty_description(self):
        service = PlaybookService(connection_pool=MagicMock())
        with pytest.raises(PlaybookValidationError, match="description"):
            service.create_playbook("c1", "ok-name", "  ", "body")

    def test_validate_rejects_oversized_body(self):
        service = PlaybookService(connection_pool=MagicMock())
        with pytest.raises(PlaybookValidationError, match="exceeds"):
            service.create_playbook("c1", "ok-name", "desc", "x" * 16385)

    def test_validate_rejects_nul_in_body(self):
        # A NUL (0x00) cannot be stored in a Postgres TEXT column; it must be rejected as a
        # clean ValidationError (-> HTTP 400), not reach the INSERT and surface as a 500.
        service = PlaybookService(connection_pool=MagicMock())
        with pytest.raises(PlaybookValidationError, match="NUL"):
            service.create_playbook("c1", "ok-name", "desc", "body with \x00 nul")

    def test_validate_accepts_multiline_body(self):
        # Unlike the single-line description, a body is multi-line markdown — newlines/tabs
        # are valid and must NOT be rejected (only NUL is screened).
        PlaybookService._validate("ok-name", "one line desc", "line1\nline2\twith tab")

    def test_validate_rejects_oversized_description(self):
        # the Agent Skills spec limit is 1024 chars
        service = PlaybookService(connection_pool=MagicMock())
        with pytest.raises(PlaybookValidationError, match="description exceeds"):
            service.create_playbook("c1", "ok-name", "d" * 1025, "body")

    def test_validate_accepts_spec_sized_description(self):
        # 1024 chars is valid under the spec; only the DB call should be reached.
        pool = MagicMock()
        service = PlaybookService(connection_pool=pool)
        try:
            service.create_playbook("c1", "ok-name", "d" * 1024, "body")
        except PlaybookValidationError as exc:  # pragma: no cover - regression guard
            pytest.fail(f"1024-char description rejected: {exc}")
        except Exception:
            pass  # mocked-DB fallout is fine; validation passed

    def test_validate_rejects_multiline_description(self):
        # public descriptions render into OTHER users' system prompts: a newline could
        # forge extra listing lines there
        service = PlaybookService(connection_pool=MagicMock())
        for bad in ("line1\nline2", "tab\there", "bell\x07"):
            with pytest.raises(PlaybookValidationError, match="single line"):
                service.create_playbook("c1", "ok-name", bad, "body")

    def test_list_playbooks_without_bodies_skips_body_column(self):
        pool = MagicMock()
        conn = pool.get_connection_direct.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        PlaybookService(connection_pool=pool).list_playbooks("c1", with_bodies=False)
        sql = cursor.execute.call_args[0][0]
        assert "'' AS body" in sql

    def test_validate_rejects_bad_visibility(self):
        service = PlaybookService(connection_pool=MagicMock())
        with pytest.raises(PlaybookValidationError, match="visibility"):
            service.create_playbook("c1", "ok-name", "desc", "body", visibility="everyone")

    def test_create_playbook_forwards_visibility(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.side_effect = [{"n": 0}, {
            "id": 7, "name": "shared-run", "description": "d", "body": "b",
            "owner_id": "c1", "visibility": "public",
            "created_at": None, "updated_at": None,
        }]
        service = PlaybookService(connection_pool=mock_pool)
        playbook = service.create_playbook("c1", "shared-run", "d", "b", visibility="public")
        assert playbook.visibility == "public"
        sql, params = cursor.execute.call_args[0]  # last execute = the INSERT
        assert "visibility" in sql and "public" in params

    def test_list_playbooks_includes_public_rows(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchall.return_value = []
        PlaybookService(connection_pool=mock_pool).list_playbooks("c1")
        sql, params = cursor.execute.call_args[0]
        # own OR public filter, plus the own-first ordering param
        assert "visibility = 'public'" in sql
        assert params.count("c1") == 2

    def test_get_playbook_by_name_public_lookup_prefers_own(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {
            "id": 1, "name": "a", "description": "d", "body": "b",
            "owner_id": "c1", "visibility": "public", "created_at": None, "updated_at": None,
        }
        PlaybookService(connection_pool=mock_pool).get_playbook_by_name("c1", "a", include_public=True)
        sql, params = cursor.execute.call_args[0]
        assert "visibility = 'public'" in sql
        assert "ORDER BY (owner_id = %s) DESC" in sql

    def test_create_playbook_returns_playbook(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        # first fetchone serves the per-owner count check, second the INSERT .. RETURNING row
        cursor.fetchone.side_effect = [{"n": 0}, {
            "id": 7, "name": "rucio-triage", "description": "triage transfers",
            "body": "step 1...", "owner_id": "c1",
            "created_at": datetime.now(), "updated_at": datetime.now(),
        }]
        service = PlaybookService(connection_pool=mock_pool)
        playbook = service.create_playbook("c1", "rucio-triage", "triage transfers", "step 1...")
        assert playbook.id == 7
        assert playbook.name == "rucio-triage"
        conn.commit.assert_called()

    def test_create_playbook_duplicate_raises_conflict(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {"n": 0}
        # count query succeeds; the INSERT itself hits the unique index
        cursor.execute.side_effect = [None, pg_errors.UniqueViolation()]
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookConflictError, match="already exists"):
            service.create_playbook("c1", "dupe-name", "desc", "body")
        conn.rollback.assert_called()

    def test_create_playbook_rejects_at_owner_cap(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {"n": 100}
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookValidationError, match="limit reached"):
            service.create_playbook("c1", "one-too-many", "desc", "body")
        # only the count query ran — the INSERT was never attempted
        assert cursor.execute.call_count == 1
        conn.commit.assert_not_called()

    def test_list_playbooks_returns_list(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchall.return_value = [
            {"id": 1, "name": "a", "description": "da", "body": "ba",
             "owner_id": "c1", "created_at": None, "updated_at": None},
            {"id": 2, "name": "b", "description": "db", "body": "bb",
             "owner_id": "c1", "created_at": None, "updated_at": None},
        ]
        service = PlaybookService(connection_pool=mock_pool)
        playbooks = service.list_playbooks("c1")
        assert [s.name for s in playbooks] == ["a", "b"]

    def test_get_playbook_found(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = {
            "id": 3, "name": "c", "description": "dc", "body": "bc",
            "owner_id": "c1", "created_at": None, "updated_at": None,
        }
        service = PlaybookService(connection_pool=mock_pool)
        assert service.get_playbook("c1", 3).name == "c"

    def test_get_playbook_not_found_raises(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = None
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookNotFoundError):
            service.get_playbook("c1", 999)

    def test_get_playbook_by_name_not_found_raises(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = None
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookNotFoundError):
            service.get_playbook_by_name("c1", "nope")

    def test_update_playbook_commits(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        existing = {"id": 4, "name": "old", "description": "d", "body": "b",
                    "owner_id": "c1", "created_at": None, "updated_at": None}
        updated = {**existing, "name": "new"}
        cursor.fetchone.side_effect = [existing, updated]  # get_playbook, then UPDATE RETURNING
        service = PlaybookService(connection_pool=mock_pool)
        result = service.update_playbook("c1", 4, name="new")
        assert result.name == "new"
        conn.commit.assert_called()

    def test_update_playbook_not_found_raises(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.fetchone.return_value = None  # get_playbook finds nothing
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookNotFoundError):
            service.update_playbook("c1", 4, name="new")

    def test_delete_playbook_removes_row(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.rowcount = 1
        service = PlaybookService(connection_pool=mock_pool)
        service.delete_playbook("c1", 4)
        conn.commit.assert_called()

    # ── IDOR invariant: every single-owner query is owner-scoped in SQL + params ──

    def test_read_queries_are_owner_scoped(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        service = PlaybookService(connection_pool=mock_pool)
        row = {"id": 1, "name": "a", "description": "d", "body": "b",
               "owner_id": "c1", "created_at": None, "updated_at": None}
        cursor.fetchall.return_value = []
        service.list_playbooks("c1")
        sql, params = cursor.execute.call_args[0]
        assert "owner_id = %s" in sql and "c1" in params
        cursor.fetchone.return_value = row
        service.get_playbook("c1", 1)
        sql, params = cursor.execute.call_args[0]
        assert "owner_id = %s" in sql and "c1" in params and 1 in params
        service.get_playbook_by_name("c1", "a")
        sql, params = cursor.execute.call_args[0]
        assert "owner_id = %s" in sql and "c1" in params

    def test_delete_is_owner_scoped(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.rowcount = 1
        PlaybookService(connection_pool=mock_pool).delete_playbook("c1", 7)
        sql, params = cursor.execute.call_args[0]
        assert "owner_id = %s" in sql and "c1" in params and 7 in params

    def test_update_is_owner_scoped(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        existing = {"id": 4, "name": "old", "description": "d", "body": "b",
                    "owner_id": "c1", "created_at": None, "updated_at": None}
        cursor.fetchone.side_effect = [existing, {**existing, "name": "new"}]
        PlaybookService(connection_pool=mock_pool).update_playbook("c1", 4, name="new")
        sql, params = cursor.execute.call_args[0]  # last execute = the UPDATE
        assert "owner_id = %s" in sql and "c1" in params and 4 in params

    def test_delete_playbook_not_found_raises(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        cursor.rowcount = 0
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookNotFoundError):
            service.delete_playbook("c1", 999)

    def test_update_playbook_conflict_raises(self, mock_pool, mock_connection):
        conn, cursor = mock_connection
        existing = {"id": 4, "name": "old", "description": "d", "body": "b",
                    "owner_id": "c1", "created_at": None, "updated_at": None}
        cursor.fetchone.side_effect = [existing]            # get_playbook succeeds
        cursor.execute.side_effect = [None, pg_errors.UniqueViolation()]  # SELECT ok, UPDATE conflicts
        service = PlaybookService(connection_pool=mock_pool)
        with pytest.raises(PlaybookConflictError, match="already exists"):
            service.update_playbook("c1", 4, name="other-existing")
        conn.rollback.assert_called()

    def test_playbook_invocation_text_uses_command_block(self):
        from src.utils.playbook_service import playbook_invocation_text
        out = playbook_invocation_text("do the thing", "deploy-checklist", "PLAYBOOK BODY")
        # Claude Code slash-command expansion: command tags carry the invocation,
        # the body follows; without $ARGUMENTS the text is appended as ARGUMENTS:.
        assert out.startswith("<command-message>deploy-checklist is running…</command-message>")
        assert "<command-name>/deploy-checklist</command-name>" in out
        assert "<command-args>do the thing</command-args>" in out
        assert "PLAYBOOK BODY" in out
        assert "ARGUMENTS: do the thing" in out

    def test_playbook_invocation_text_substitutes_arguments(self):
        from src.utils.playbook_service import playbook_invocation_text
        out = playbook_invocation_text("T2_US_MIT", "site-check", "inspect $ARGUMENTS closely")
        assert "inspect T2_US_MIT closely" in out
        assert "ARGUMENTS:" not in out  # placeholder consumed the args

    def test_playbook_invocation_text_fences_foreign_body(self):
        from src.utils.playbook_service import playbook_invocation_text
        out = playbook_invocation_text("go", "theirs", "BODY", foreign=True)
        assert "Public playbook shared by another user" in out

    def test_playbook_invocation_text_empty_body_unchanged(self):
        from src.utils.playbook_service import playbook_invocation_text
        assert playbook_invocation_text("hi", "x", "") == "hi"

    def test_render_and_parse_playbook_md_round_trip(self):
        from src.utils.playbook_service import render_playbook_md, parse_playbook_md
        md = render_playbook_md("rucio-triage", "Triage stuck transfers. Use when…", "Step 1\nStep 2", "public")
        parsed = parse_playbook_md(md)
        assert parsed == {
            "name": "rucio-triage",
            "description": "Triage stuck transfers. Use when…",
            "body": "Step 1\nStep 2",
            "visibility": "public",
        }

    def test_parse_playbook_md_defaults_and_fallbacks(self):
        from src.utils.playbook_service import parse_playbook_md
        md = "---\ndescription: d\n---\n\nBODY\n"
        parsed = parse_playbook_md(md, fallback_name="from-folder")
        assert parsed["name"] == "from-folder"
        assert parsed["visibility"] == "private"
        # unknown frontmatter keys are tolerated (spec allows extras)
        md2 = "---\nname: a\ndescription: d\nlicense: MIT\n---\nB"
        assert parse_playbook_md(md2)["name"] == "a"

    def test_parse_playbook_md_rejects_missing_frontmatter(self):
        from src.utils.playbook_service import parse_playbook_md
        with pytest.raises(PlaybookValidationError, match="frontmatter"):
            parse_playbook_md("just a body, no frontmatter")

    def test_parse_playbook_md_ignores_indented_fence(self):
        # an indented '---' is YAML content (block-scalar continuation), not a fence
        from src.utils.playbook_service import parse_playbook_md
        md = "---\nname: a\ndescription: |\n  part one\n  ---\n  part two\n---\nBODY"
        parsed = parse_playbook_md(md)
        assert parsed["name"] == "a"
        assert "part two" in parsed["description"]
        assert parsed["body"] == "BODY"

    def test_parse_playbook_md_non_string_name_is_rejected_not_renamed(self):
        """YAML 1.1 'Norway problem': unquoted no/off/false parse as bool and 0
        as int — all four are valid slugs, and the old truthiness fallback
        silently imported the playbook under the folder/file name (and
        overwrite-on-conflict then targeted the wrong entry). A non-string
        name must be a loud per-item error telling the user to quote it."""
        from src.utils.playbook_service import parse_playbook_md
        for literal in ("no", "off", "false", "0"):
            md = f"---\nname: {literal}\ndescription: d\n---\nBODY"
            with pytest.raises(PlaybookValidationError, match="quote"):
                parse_playbook_md(md, fallback_name="from-folder")

    def test_parse_playbook_md_quoted_reserved_word_name_is_kept(self):
        from src.utils.playbook_service import parse_playbook_md
        md = '---\nname: "no"\ndescription: d\n---\nBODY'
        assert parse_playbook_md(md, fallback_name="from-folder")["name"] == "no"

    def test_parse_playbook_md_non_string_description_is_rejected(self):
        """Same coercion on description would silently store 'False'."""
        from src.utils.playbook_service import parse_playbook_md
        md = "---\nname: a\ndescription: off\n---\nBODY"
        with pytest.raises(PlaybookValidationError, match="quote"):
            parse_playbook_md(md)

    def test_pending_playbook_contextvar_roundtrip(self):
        from src.archi.pipelines.agents.tools.playbook_tools import (
            set_pending_playbook, get_pending_playbook, clear_pending_playbook,
        )
        clear_pending_playbook()
        assert get_pending_playbook() is None
        set_pending_playbook("deploy-checklist", "BODY")
        assert get_pending_playbook() == {"name": "deploy-checklist", "body": "BODY", "foreign": False, "playbook_id": None}
        set_pending_playbook("public-runbook", "B", foreign=True)
        assert get_pending_playbook()["foreign"] is True
        clear_pending_playbook()
        assert get_pending_playbook() is None

    def test_get_connection_uses_direct_accessor_with_pool(self):
        # ConnectionPool.get_connection() is a @contextmanager; PlaybookService manages
        # the conn manually, so it must use the raw accessor get_connection_direct().
        pool = MagicMock()
        svc = PlaybookService(connection_pool=pool)
        svc._get_connection()
        pool.get_connection_direct.assert_called_once()
        pool.get_connection.assert_not_called()

    # ── Boundary: name length ──────────────────────────────────────────────────

    def test_validate_accepts_name_exactly_64_chars(self):
        # Spec limit: max 64 chars inclusive — the 64th character must NOT be rejected.
        name_64 = "a" * 32 + "-" + "b" * 31   # 64 chars, valid kebab-case
        assert len(name_64) == 64
        PlaybookService._validate(name_64, "desc", "body")  # must not raise

    def test_validate_rejects_name_65_chars(self):
        # One character over the limit is rejected regardless of content.
        name_65 = "a" * 32 + "-" + "b" * 32   # 65 chars
        assert len(name_65) == 65
        with pytest.raises(PlaybookValidationError, match="64"):
            PlaybookService._validate(name_65, "desc", "body")

    # ── Boundary: body length ──────────────────────────────────────────────────

    def test_validate_accepts_body_exactly_16384_chars(self):
        # MAX_BODY_CHARS == 16384; the boundary value itself must be accepted.
        PlaybookService._validate("ok", "desc", "x" * 16384)  # must not raise

    # (16385 already tested by test_validate_rejects_oversized_body)

    # ── Boundary: description length ──────────────────────────────────────────

    def test_validate_accepts_description_exactly_1024_chars(self):
        # MAX_DESCRIPTION_CHARS == 1024; the boundary value must be accepted.
        # (The description regex also rejects control chars; use plain ASCII.)
        PlaybookService._validate("ok", "d" * 1024, "body")  # must not raise

    # (1025 already tested by test_validate_rejects_oversized_description)

    # ── Control chars in description ──────────────────────────────────────────

    def test_validate_rejects_tab_in_description(self):
        # A tab (\x09) is a control character — descriptions embed in system prompts
        # where tabs would corrupt whitespace layout.
        with pytest.raises(PlaybookValidationError, match="single line"):
            PlaybookService._validate("ok", "desc\there", "body")

    def test_validate_rejects_carriage_return_in_description(self):
        # A CR (\x0d) can forge line breaks in rendered system-prompt output.
        with pytest.raises(PlaybookValidationError, match="single line"):
            PlaybookService._validate("ok", "desc\rhere", "body")

    # ── NUL in name ───────────────────────────────────────────────────────────

    def test_validate_rejects_nul_in_name(self):
        # NUL is not in [a-z0-9\-], so _NAME_RE already rejects it.  The error
        # message references "lowercase" (the name-format rule), not "NUL", which
        # is fine because the root cause is an invalid character, not the NUL check.
        with pytest.raises(PlaybookValidationError, match="lowercase"):
            PlaybookService._validate("na\x00me", "desc", "body")

    # ── Owner cap boundary: 99 existing → allows creation ─────────────────────

    def test_create_playbook_allows_at_99_existing(self, mock_pool, mock_connection):
        # One below the cap (99) must proceed to the INSERT, not raise.
        conn, cursor = mock_connection
        cursor.fetchone.side_effect = [
            {"n": 99},      # count query: 99 existing, under the 100 limit
            {"id": 10, "name": "new-book", "description": "d", "body": "b",
             "owner_id": "c1", "visibility": "private",
             "created_at": None, "updated_at": None},
        ]
        service = PlaybookService(connection_pool=mock_pool)
        playbook = service.create_playbook("c1", "new-book", "d", "b")
        assert playbook.id == 10
        # The INSERT was attempted (2 execute calls: COUNT + INSERT)
        assert cursor.execute.call_count == 2

    # ── update: no-op (no fields changed) ────────────────────────────────────

    def test_update_playbook_noop_unchanged(self, mock_pool, mock_connection):
        # Calling update_playbook with no keyword arguments must still succeed:
        # the service re-applies the existing values and commits the row.
        conn, cursor = mock_connection
        existing = {"id": 5, "name": "stable", "description": "same desc",
                    "body": "same body", "owner_id": "c1", "visibility": "private",
                    "created_at": None, "updated_at": None}
        cursor.fetchone.side_effect = [existing, existing]  # get_playbook, UPDATE RETURNING
        service = PlaybookService(connection_pool=mock_pool)
        result = service.update_playbook("c1", 5)   # no kwargs → no-op values
        assert result.name == "stable"
        conn.commit.assert_called()

    # ── get_playbook_by_name shadowing: own row wins over a public row ────────

    def test_get_playbook_by_name_returns_owners_row_when_public_also_exists(
        self, mock_pool, mock_connection
    ):
        # When the DB (correctly ordered) returns the owner's own row first,
        # get_playbook_by_name must return that row — not a foreign public one.
        conn, cursor = mock_connection
        owner_row = {
            "id": 11, "name": "deploy", "description": "my version", "body": "own body",
            "owner_id": "c1", "visibility": "private", "created_at": None, "updated_at": None,
        }
        # The real ORDER BY (owner_id = %s) DESC places the owner's row first;
        # fetchone picks the first row, which is the owner's.
        cursor.fetchone.return_value = owner_row
        service = PlaybookService(connection_pool=mock_pool)
        result = service.get_playbook_by_name("c1", "deploy", include_public=True)
        # Must be the owner's row, not any hypothetical public row.
        assert result.id == 11
        assert result.owner_id == "c1"
        assert result.description == "my version"
        # Verify the SQL sends owner_id twice (for WHERE and for ORDER BY)
        sql, params = cursor.execute.call_args[0]
        assert params.count("c1") == 2
        assert "ORDER BY (owner_id = %s) DESC" in sql

    # ── SQL_INSERT_PLAYBOOK_TURN idempotency ──────────────────────────────────

    def test_sql_insert_playbook_turn_is_idempotent_upsert(self):
        # The side-table INSERT must be safe to replay with the same message_id.
        from src.utils import sql
        assert "ON CONFLICT (message_id) DO NOTHING" in sql.SQL_INSERT_PLAYBOOK_TURN

    # ── render/parse: multi-line body round-trip ──────────────────────────────

    def test_render_parse_round_trip_multiline_body(self):
        from src.utils.playbook_service import render_playbook_md, parse_playbook_md
        multi_body = "Step 1: Do X\n\nStep 2: Do Y\n  - sub item\nStep 3: Done"
        md = render_playbook_md("my-playbook", "A useful description.", multi_body, "public")
        parsed = parse_playbook_md(md)
        assert parsed["name"] == "my-playbook"
        assert parsed["body"] == multi_body
        assert parsed["visibility"] == "public"

    # ── parse: unclosed frontmatter fence ────────────────────────────────────

    def test_parse_playbook_md_rejects_unclosed_frontmatter(self):
        from src.utils.playbook_service import parse_playbook_md
        # A SKILL.md that opens '---' but never closes it is malformed.
        with pytest.raises(PlaybookValidationError, match="frontmatter"):
            parse_playbook_md("---\nname: a\ndescription: d\n")

    # ── parse: top-level visibility key (not under metadata) ──────────────────

    def test_parse_playbook_md_reads_top_level_visibility(self):
        # Some exporters may emit 'visibility' at the top level (not nested under
        # 'metadata').  The parser accepts both layouts.
        from src.utils.playbook_service import parse_playbook_md
        md = "---\nname: a\ndescription: d\nvisibility: public\n---\nBODY"
        parsed = parse_playbook_md(md)
        assert parsed["visibility"] == "public"


# =============================================================================
# TestResolvePlaybookOwner Tests
# =============================================================================

class TestResolvePlaybookOwner:
    """Tests for resolve_playbook_owner — session-identity guard for playbook IDOR mitigation."""

    def test_authed_logged_in_email_returns_email(self):
        """When auth is on and user is logged in with email, return the email."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"email": "alice@example.com", "name": "Alice"},
            request_client_id="some-uuid-from-frontend",
        )
        assert owner == "alice@example.com"
        assert err is None

    def test_authed_logged_in_different_client_id_ignored_not_rejected(self):
        """When auth on and logged in with email, a different request client_id is IGNORED.

        This is the IDOR fix: the frontend legitimately sends a UUID client_id that
        never equals the SSO email — rejecting it would break authed requests.
        The server-verified identity wins and the supplied client_id is silently ignored.
        """
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"email": "alice@example.com"},
            request_client_id="attacker-or-unrelated-uuid",
        )
        # Must return the session email, not the request client_id, and no error
        assert owner == "alice@example.com"
        assert err is None
        assert owner != "attacker-or-unrelated-uuid"

    def test_authed_logged_in_no_email_falls_back_to_sub(self):
        """When logged in but no email, use sub as verified identity."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"sub": "sub|12345"},
            request_client_id="frontend-uuid",
        )
        assert owner == "sub|12345"
        assert err is None

    def test_authed_logged_in_name_only_fails_closed(self):
        """A display 'name' is not unique, so it is NOT a valid owner key: a session
        carrying only a name (no email/sub/id) must fail closed, not resolve to the name."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"name": "Bob"},
            request_client_id="frontend-uuid",
        )
        assert owner is None    # 'name' is no longer a fallback (not unique)
        assert err              # error returned (fail closed)

    def test_authed_logged_in_empty_session_user_fails_closed(self):
        """Logged in but session_user has no usable identity -> fail closed, do NOT trust client_id."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={},
            request_client_id="frontend-uuid",
        )
        assert owner is None   # IDOR not re-opened
        assert err             # error returned

    def test_authed_not_logged_in_uses_request_client_id(self):
        """Auth enabled but user not logged in → anonymous, use request client_id."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=False,
            session_user=None,
            request_client_id="anon-uuid",
        )
        assert owner == "anon-uuid"
        assert err is None

    def test_auth_disabled_uses_request_client_id(self):
        """Auth disabled (anonymous deployment) → always use request client_id."""
        owner, err = resolve_playbook_owner(
            auth_enabled=False,
            logged_in=False,
            session_user=None,
            request_client_id="anon-uuid",
        )
        assert owner == "anon-uuid"
        assert err is None

    def test_anon_no_client_id_returns_error(self):
        """Anonymous + no client_id → rejectable error."""
        owner, err = resolve_playbook_owner(
            auth_enabled=False,
            logged_in=False,
            session_user=None,
            request_client_id=None,
        )
        assert owner is None
        assert err == "client_id is required"

    def test_anon_nul_client_id_returns_error(self):
        """A client_id containing NUL (0x00) → rejectable error, not an unhandled 500.

        owner_id is used directly as a Postgres string parameter, which cannot contain NUL;
        rejecting it here keeps every endpoint returning a clean 400 instead of a 500.
        """
        owner, err = resolve_playbook_owner(
            auth_enabled=False,
            logged_in=False,
            session_user=None,
            request_client_id="client\x00id",
        )
        assert owner is None
        assert "NUL" in err

    def test_anon_non_string_client_id_returns_error(self):
        """A non-string client_id (any JSON type) → rejectable error, never an
        unhandled exception.

        A dict slips past the truthiness and NUL guards ('\\x00' in a dict
        checks keys) and psycopg2 cannot adapt it at bind time; an int makes
        the NUL membership test itself raise TypeError. Both must become a
        clean 400-path error instead of a blanket 500.
        """
        for bad_client_id in ({"x": 1}, 123, ["c1"], True):
            owner, err = resolve_playbook_owner(
                auth_enabled=False,
                logged_in=False,
                session_user=None,
                request_client_id=bad_client_id,
            )
            assert owner is None, f"owner must not be {bad_client_id!r}"
            assert "string" in err

    def test_authed_not_logged_in_no_client_id_returns_error(self):
        """Auth enabled, not logged in, no client_id supplied → rejectable error."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=False,
            session_user=None,
            request_client_id=None,
        )
        assert owner is None
        assert err == "client_id is required"

    def test_authed_logged_in_oidc_id_shape_returns_id(self):
        # OIDC callback stores the subject claim under 'id' (not 'sub'), no email.
        owner, err = resolve_playbook_owner(True, True, {"id": "sub|12345", "email": "", "name": ""}, "attacker-uuid")
        assert owner == "sub|12345"
        assert err is None

    def test_auth_disabled_logged_in_uses_request_client_id(self):
        """Auth disabled always ignores session state and uses the request client_id.

        An unusual-but-possible state: auth_enabled=False yet logged_in=True (e.g.
        the frontend still has a stale session flag after an operator toggle). The
        function must fall through to the anonymous branch and trust the client_id.
        """
        owner, err = resolve_playbook_owner(
            auth_enabled=False,
            logged_in=True,
            session_user={"email": "alice@example.com"},
            request_client_id="anon-cid",
        )
        assert owner == "anon-cid"
        assert err is None

    def test_authed_logged_in_session_user_none_fails_closed(self):
        """auth_enabled=True, logged_in=True but session_user is None → fail closed.

        session_user=None means the session dict was never set; the service must NOT
        fall back to request_client_id (that would re-open the IDOR).
        """
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user=None,
            request_client_id="attacker-uuid",
        )
        assert owner is None
        assert err is not None
        assert owner != "attacker-uuid"

    def test_anon_empty_string_client_id_returns_error(self):
        """An empty-string client_id is treated the same as None (missing).

        Empty strings are falsy in Python, so the `if not request_client_id` guard
        fires for both None and "".
        """
        owner, err = resolve_playbook_owner(
            auth_enabled=False,
            logged_in=False,
            session_user=None,
            request_client_id="",
        )
        assert owner is None
        assert err == "client_id is required"


def test_side_table_sql_queries_defined():
    from src.utils import sql
    assert "conversation_playbook_turns" in sql.SQL_INSERT_PLAYBOOK_TURN
    assert "ON CONFLICT" in sql.SQL_INSERT_PLAYBOOK_TURN
    assert "conversation_playbook_turns" in sql.SQL_LAST_PLAYBOOK_NAME_FOR_SENDER


def test_sql_insert_convo_is_nine_shared_columns():
    from src.utils import sql
    assert "playbook_name" not in sql.SQL_INSERT_CONVO
    for col in ("archi_service", "conversation_id", "sender", "content",
                "link", "context", "ts", "model_used", "pipeline_used"):
        assert col in sql.SQL_INSERT_CONVO


def test_conversation_service_insert_tuple_is_nine_fields(mock_pool, mock_connection):
    import src.utils.conversation_service as cs
    import psycopg2.extras
    captured = {}
    def fake_execute_values(cur, sql, values, *a, **k):
        captured["values"] = values
        return []
    svc = cs.ConversationService(connection_pool=mock_pool)
    msg = cs.Message(archi_service="chat", conversation_id=1, sender="User",
                     content="hi", link="", context="", model_used="m", pipeline_used="p")
    orig = cs.execute_values
    cs.execute_values = fake_execute_values
    try:
        svc.insert_messages([msg])
    except Exception:
        pass  # mocked-pool fallout is fine; we only care about captured values
    finally:
        cs.execute_values = orig
    assert "values" in captured
    assert len(captured["values"][0]) == 9


def test_load_query_reads_playbook_from_side_table():
    from src.utils import sql
    assert "conversation_playbook_turns cpt" in sql.SQL_QUERY_CONVO_WITH_FEEDBACK
    assert "cpt.playbook_name" in sql.SQL_QUERY_CONVO_WITH_FEEDBACK
    assert "c.playbook_name" not in sql.SQL_QUERY_CONVO_WITH_FEEDBACK


def test_load_query_fallback_variant_omits_side_table():
    """M1 guard: when the side-table migration failed, conversation loads fall
    back to this variant — it must not touch conversation_playbook_turns and
    must keep the exact column shape of the primary query (playbook_name last,
    NULL) so the row-unpacking code works unchanged."""
    from src.utils import sql
    fallback = sql.SQL_QUERY_CONVO_WITH_FEEDBACK_NO_PLAYBOOKS
    assert "conversation_playbook_turns" not in fallback
    assert "NULL AS playbook_name" in fallback
    for shared_expr in ("c.sender", "c.content", "c.message_id",
                        "lf.feedback", "comment_count", "c.model_used"):
        assert shared_expr in fallback
    assert "WHERE c.conversation_id = %s" in fallback
    assert "ORDER BY c.message_id ASC" in fallback


def test_side_table_access_lives_on_playbook_service_not_app():
    """Per-turn side-table access and the schema migration live on PlaybookService,
    not on the Flask wrapper: app.py must not define the raw-SQL helpers and must
    not embed side-table SQL inline.

    app.py cannot be imported in the unit-test environment (mistune, flask etc. absent), so
    we verify the source text directly via the raw file; the service methods themselves are
    behaviorally covered in test_playbook_service.py."""
    import pathlib

    from src.utils.playbook_service import PlaybookService

    app_src = (
        pathlib.Path(__file__).parent.parent.parent
        / "src" / "interfaces" / "chat_app" / "app.py"
    ).read_text()

    for moved_def in (
        "def _last_user_playbook_name(",
        "def _insert_playbook_turn(",
        "def _ensure_playbook_schema(",
    ):
        assert moved_def not in app_src, f"{moved_def} moved to PlaybookService; app.py has it back"
    assert "SELECT playbook_name FROM conversations" not in app_src, (
        "app.py contains inline side-table SQL again"
    )
    assert "INSERT INTO conversation_playbook_turns" not in app_src, (
        "app.py contains inline side-table SQL again"
    )

    for service_method in ("record_playbook_turn", "last_playbook_name_for_sender", "ensure_schema"):
        assert callable(getattr(PlaybookService, service_method, None)), (
            f"PlaybookService.{service_method} is missing"
        )


def test_init_sql_conversations_has_no_playbook_name():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for rel in ("src/cli/templates/init.sql", "tests/smoke/init-test.sql"):
        text = (root / rel).read_text()
        start = text.index("CREATE TABLE IF NOT EXISTS conversations")
        end = text.index(");", start)
        assert "playbook_name" not in text[start:end], rel


def test_init_sql_creates_playbook_invocations_and_grants_grafana():
    """The unified ledger table exists in init.sql (fresh installs), is granted to
    the Grafana read-only role, and never declares an owner_id column."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    text = (root / "src/cli/templates/init.sql").read_text()
    start = text.index("CREATE TABLE IF NOT EXISTS playbook_invocations")
    end = text.index(");", start)
    ddl = text[start:end]
    assert "owner_id" not in ddl, "ledger must have no owner_id column"
    assert "source" in ddl and "status" in ddl and "arm" in ddl
    # Granted SELECT to grafana. A dedicated grant (co-located after the CREATE) is
    # required here: the section-11 bulk grant runs before the playbook tables exist.
    assert re.search(
        r"GRANT SELECT ON[^;]*\bplaybook_invocations\b[^;]*TO grafana;", text, re.S
    ), "playbook_invocations must be granted SELECT to grafana"
    # index on (playbook_name, ts)
    assert "idx_playbook_invocations" in text


def test_init_sql_and_ensure_schema_ledger_columns_match():
    """The init.sql table and PlaybookService.ensure_schema declare the same
    ledger columns (they must not drift — DB source of truth vs Helm boot path)."""
    from pathlib import Path
    import inspect
    from src.utils.playbook_service import PlaybookService
    root = Path(__file__).resolve().parents[2]
    init_sql = (root / "src/cli/templates/init.sql").read_text()
    ensure_src = inspect.getsource(PlaybookService.ensure_schema)
    for token in ("playbook_invocations", "conversation_id", "message_id",
                  "playbook_id", "playbook_name", "source", "status", "arm"):
        assert token in init_sql, f"init.sql missing {token}"
        assert token in ensure_src, f"ensure_schema missing {token}"


# =============================================================================
# New gap-filling tests (Categories 1-6)
# =============================================================================

class TestResolvePlaybookOwnerGaps:
    """Additional resolve_playbook_owner tests covering gaps not addressed above."""

    def test_authed_logged_in_full_session_prefers_id(self):
        """When session_user has email, sub, id, and name all set, the OIDC subject 'id' wins —
        it is the key persisted as users.id and conversation_metadata.user_id, so playbook
        ownership stays consistent with the rest of the identity model. email must NOT win
        (it is mutable; precedence is id > sub > email; 'name' is never an owner key)."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"email": "a@b.c", "sub": "S", "id": "I", "name": "N"},
            request_client_id="different-uuid",
        )
        assert owner == "I"            # the OIDC subject ('id'), matching users.id
        assert owner != "a@b.c"        # email must not win — mutable, would diverge from users.id
        assert err is None
        # client_id must be ignored — the IDOR fix
        assert owner != "different-uuid"

    def test_authed_logged_in_id_fallback_when_no_email_or_sub(self):
        """When session_user has only 'id', it must be used (precedence is id > sub > email;
        'name' is never an owner key)."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"id": "oidc-subject-id"},
            request_client_id="ignored-uuid",
        )
        assert owner == "oidc-subject-id"
        assert err is None

    def test_authed_logged_in_empty_email_sub_id_fails_closed(self):
        """Empty strings for email/sub/id are falsy, and 'name' is no longer a fallback
        (not unique) → the session has no usable identity and must fail closed."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=True,
            session_user={"email": "", "sub": "", "id": "", "name": "FallbackName"},
            request_client_id="ignored-uuid",
        )
        assert owner is None    # 'name' not used; fail closed
        assert err              # error returned

    def test_auth_disabled_logged_in_true_nul_client_id_rejected(self):
        """Auth disabled branch: NUL in client_id is rejected even when logged_in=True."""
        owner, err = resolve_playbook_owner(
            auth_enabled=False,
            logged_in=True,
            session_user={"email": "a@b.c"},
            request_client_id="bad\x00id",
        )
        assert owner is None
        assert "NUL" in err

    def test_authed_not_logged_in_nul_client_id_rejected(self):
        """Auth enabled, not logged in branch: NUL in client_id still rejected."""
        owner, err = resolve_playbook_owner(
            auth_enabled=True,
            logged_in=False,
            session_user=None,
            request_client_id="cli\x00ent",
        )
        assert owner is None
        assert "NUL" in err


class TestValidateGaps:
    """Gap tests for PlaybookService._validate not covered by existing tests."""

    def test_validate_name_with_trailing_newline_rejected(self):
        """\\Z anchor: 'ok-name\\n' must be rejected even though 'ok-name' would pass."""
        with pytest.raises(PlaybookValidationError, match="lowercase"):
            PlaybookService._validate("ok-name\n", "desc", "body")

    def test_validate_accepts_valid_name_with_number(self):
        """'valid-name-1' is a perfectly legal kebab-case name; must not raise."""
        PlaybookService._validate("valid-name-1", "A good description", "body text")

    def test_validate_accepts_single_segment_name(self):
        """A single lowercase word with no hyphens is valid."""
        PlaybookService._validate("abc", "desc", "body")

    def test_validate_rejects_empty_name(self):
        """An empty string for name must be rejected (falsy check before regex)."""
        with pytest.raises(PlaybookValidationError, match="lowercase"):
            PlaybookService._validate("", "desc", "body")

    def test_validate_rejects_del_char_in_description(self):
        """chr(0x7f) DEL is a control character and must be rejected in descriptions."""
        with pytest.raises(PlaybookValidationError, match="single line"):
            PlaybookService._validate("ok", "desc\x7fhere", "body")

    def test_validate_rejects_nul_in_description(self):
        """NUL (0x00) in description falls under the control-char regex [\\x00-\\x1f\\x7f]."""
        with pytest.raises(PlaybookValidationError, match="single line"):
            PlaybookService._validate("ok", "desc\x00here", "body")

    def test_validate_rejects_empty_body(self):
        """Empty body string must be rejected."""
        with pytest.raises(PlaybookValidationError, match="body"):
            PlaybookService._validate("ok", "desc", "")

    def test_validate_rejects_whitespace_only_body(self):
        """A body of only spaces/tabs must be rejected (body.strip() is falsy)."""
        with pytest.raises(PlaybookValidationError, match="body"):
            PlaybookService._validate("ok", "desc", "   \t\n   ")

    def test_validate_accepts_private_visibility(self):
        """'private' is a valid visibility; must not raise."""
        PlaybookService._validate("ok", "desc", "body", "private")

    def test_validate_accepts_public_visibility(self):
        """'public' is a valid visibility; must not raise."""
        PlaybookService._validate("ok", "desc", "body", "public")

    def test_validate_rejects_team_visibility(self):
        """'team' is not an accepted visibility value — _validate rejects it (no longer aliased to public)."""
        with pytest.raises(PlaybookValidationError, match="visibility"):
            PlaybookService._validate("ok", "desc", "body", "team")

    def test_validate_rejects_uppercase_only_name(self):
        """Uppercase letters are not in [a-z0-9], must be rejected."""
        with pytest.raises(PlaybookValidationError, match="lowercase"):
            PlaybookService._validate("UPPERCASE", "desc", "body")


class TestRowToPlaybookGaps:
    """Gap tests for PlaybookService._row_to_playbook not covered by existing tests."""

    def test_row_to_playbook_missing_visibility_defaults_to_private(self):
        """A row without a 'visibility' key (leaner test/mock dicts) must default to 'private'."""
        row = {
            "id": 1, "name": "x", "description": "d", "body": "b",
            "owner_id": "c1",
            "created_at": None, "updated_at": None,
            # 'visibility' deliberately absent
        }
        pb = PlaybookService._row_to_playbook(row)
        assert pb.visibility == "private"

    def test_row_to_playbook_none_timestamps_remain_none(self):
        """created_at/updated_at None in the row → Playbook.created_at/updated_at are None."""
        row = {
            "id": 2, "name": "y", "description": "d2", "body": "b2",
            "owner_id": "c2", "visibility": "public",
            "created_at": None, "updated_at": None,
        }
        pb = PlaybookService._row_to_playbook(row)
        assert pb.created_at is None
        assert pb.updated_at is None

    def test_row_to_playbook_datetime_timestamp_is_stringified(self):
        """A datetime object in created_at/updated_at is converted to str(...)."""
        ts = datetime(2025, 1, 15, 12, 30, 0)
        row = {
            "id": 3, "name": "z", "description": "d3", "body": "b3",
            "owner_id": "c3", "visibility": "private",
            "created_at": ts, "updated_at": ts,
        }
        pb = PlaybookService._row_to_playbook(row)
        assert pb.created_at == str(ts)
        assert pb.updated_at == str(ts)

    def test_row_to_playbook_string_timestamp_is_stringified(self):
        """A string timestamp value is wrapped in str() (no-op, still a string)."""
        ts_str = "2025-06-01T10:00:00"
        row = {
            "id": 4, "name": "w", "description": "d4", "body": "b4",
            "owner_id": "c4", "visibility": "public",
            "created_at": ts_str, "updated_at": ts_str,
        }
        pb = PlaybookService._row_to_playbook(row)
        assert pb.created_at == ts_str
        assert pb.updated_at == ts_str

    def test_row_to_playbook_explicit_none_visibility_defaults_to_private(self):
        """visibility=None in the row (e.g. old rows pre-migration) → 'private' via `or`."""
        row = {
            "id": 5, "name": "v", "description": "d5", "body": "b5",
            "owner_id": "c5", "visibility": None,
            "created_at": None, "updated_at": None,
        }
        pb = PlaybookService._row_to_playbook(row)
        assert pb.visibility == "private"


class TestRenderPlaybookMdGaps:
    """Gap tests for render_playbook_md not covered by existing round-trip tests."""

    def test_render_public_includes_metadata_visibility(self):
        """A public playbook must have 'metadata:' with 'visibility: public' in the YAML front."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("my-tool", "Does a thing", "Step 1", "public")
        assert "metadata:" in md
        assert "visibility: public" in md

    def test_render_private_has_no_metadata_key(self):
        """A private playbook must NOT include the 'metadata' key at all."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("my-tool", "Does a thing", "Step 1", "private")
        assert "metadata" not in md

    def test_render_default_visibility_has_no_metadata_key(self):
        """render_playbook_md(visibility omitted) defaults to private → no metadata key."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("my-tool", "Desc", "Body")
        assert "metadata" not in md

    def test_render_unicode_preserved_in_body_and_description(self):
        """Unicode in description and body must survive serialization unchanged."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("unicode-tool", "Triage résumé: naïve café", "步骤1: 完成\nStep2: Done")
        assert "résumé" in md
        assert "步骤1" in md

    def test_render_body_trailing_whitespace_is_rstripped(self):
        """Body with trailing whitespace/newlines is rstripped in the output."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("t", "d", "Body line   \n\n\n")
        # The rendered body section (after the second ---) must not have trailing blank lines
        body_section = md.split("---\n\n", 1)[1]
        assert not body_section.endswith("\n\n")
        # Body content itself is present without the trailing spaces/blank lines
        assert "Body line" in body_section

    def test_render_output_starts_with_triple_dash(self):
        """render_playbook_md output must always start with the '---' fence."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("t", "d", "b")
        assert md.startswith("---\n")

    def test_render_frontmatter_name_before_description(self):
        """Agent Skills spec: 'name' must appear before 'description' in the YAML frontmatter."""
        from src.utils.playbook_service import render_playbook_md
        md = render_playbook_md("a-tool", "A description", "body")
        # Find positions in the YAML block
        name_pos = md.index("name:")
        desc_pos = md.index("description:")
        assert name_pos < desc_pos, "name must appear before description in frontmatter"


class TestParsePlaybookMdGaps:
    """Gap tests for parse_playbook_md not covered by existing tests."""

    def test_parse_broken_yaml_frontmatter_raises_validation_error(self):
        """Structurally broken YAML (e.g. unmatched colon) → PlaybookValidationError."""
        from src.utils.playbook_service import parse_playbook_md
        malformed = "---\nname: valid\ndescription: d\nbad: : colon: x\n---\nBODY"
        with pytest.raises(PlaybookValidationError, match="YAML"):
            parse_playbook_md(malformed)

    def test_parse_frontmatter_list_not_dict_raises_validation_error(self):
        """Frontmatter that parses to a YAML list (not a dict) → PlaybookValidationError."""
        from src.utils.playbook_service import parse_playbook_md
        list_fm = "---\n- a\n- b\n---\nBODY"
        with pytest.raises(PlaybookValidationError, match="mapping"):
            parse_playbook_md(list_fm)

    def test_parse_leading_blank_lines_before_fence_tolerated(self):
        """Leading blank lines before the opening '---' are skipped (the parser loops over them)."""
        from src.utils.playbook_service import parse_playbook_md
        md = "\n\n---\nname: my-playbook\ndescription: d\n---\nBODY"
        parsed = parse_playbook_md(md)
        assert parsed["name"] == "my-playbook"
        assert parsed["body"] == "BODY"

    def test_parse_unknown_frontmatter_keys_tolerated(self):
        """Extra/unknown YAML keys in frontmatter must not raise (spec allows extras)."""
        from src.utils.playbook_service import parse_playbook_md
        md = "---\nname: a\ndescription: d\nlicense: MIT\nauthor: bob\n---\nBODY"
        parsed = parse_playbook_md(md)
        assert parsed["name"] == "a"
        assert parsed["body"] == "BODY"

    def test_parse_frontmatter_scalar_not_dict_raises_validation_error(self):
        """Frontmatter that parses to a plain scalar (e.g. just '42') → PlaybookValidationError."""
        from src.utils.playbook_service import parse_playbook_md
        scalar_fm = "---\n42\n---\nBODY"
        with pytest.raises(PlaybookValidationError, match="mapping"):
            parse_playbook_md(scalar_fm)
