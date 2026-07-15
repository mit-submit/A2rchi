"""
PostgreSQL Service Factory - Unified access to all PostgreSQL-backed services.

Provides a single entry point for initializing and accessing all database services
with shared connection pooling.
"""
from typing import Any, Dict, Optional
import os

from src.utils.connection_pool import ConnectionPool
from src.utils.config_service import ConfigService
from src.utils.conversation_service import ConversationService
from src.utils.document_selection_service import DocumentSelectionService
from src.utils.playbook_service import PlaybookService
from src.utils.user_service import UserService


class PostgresServiceFactory:
    """
    Factory for creating PostgreSQL-backed services with shared connection pooling.
    
    Usage:
        factory = PostgresServiceFactory.from_config({
            'host': 'localhost',
            'port': 5432,
            'database': 'archi',
            'user': 'postgres',
            'password': 'secret',
        })
        
        # Get services
        users = factory.user_service
        config = factory.config_service
        conversations = factory.conversation_service
        doc_selection = factory.document_selection_service
        
        # Or create from existing pool
        factory = PostgresServiceFactory(connection_pool=existing_pool)
    """
    
    _instance: Optional['PostgresServiceFactory'] = None
    
    def __init__(
        self,
        connection_pool: Optional[ConnectionPool] = None,
        connection_params: Optional[Dict[str, Any]] = None,
        encryption_key: Optional[str] = None,
    ):
        """
        Initialize the service factory.
        
        Args:
            connection_pool: Existing ConnectionPool instance
            connection_params: Database connection parameters (if no pool provided)
            encryption_key: Key for BYOK API key encryption (for UserService)
        """
        self._pool = connection_pool
        self._conn_params = connection_params
        self._encryption_key = encryption_key
        
        # Lazy-initialized services
        self._user_service: Optional[UserService] = None
        self._config_service: Optional[ConfigService] = None
        self._conversation_service: Optional[ConversationService] = None
        self._document_selection_service: Optional[DocumentSelectionService] = None
        self._playbook_service: Optional[PlaybookService] = None
    
    @classmethod
    def from_config(
        cls,
        connection_params: Dict[str, Any],
        pool_min_conn: int = 5,
        pool_max_conn: int = 20,
        encryption_key: Optional[str] = None,
    ) -> 'PostgresServiceFactory':
        """
        Create factory from connection parameters.
        
        Args:
            connection_params: Dict with host, port, database, user, password
            pool_min_conn: Minimum pool connections
            pool_max_conn: Maximum pool connections
            encryption_key: Key for BYOK API key encryption
            
        Returns:
            Configured PostgresServiceFactory
        """
        pool = ConnectionPool(
            connection_params=connection_params,
            min_conn=pool_min_conn,
            max_conn=pool_max_conn,
        )
        
        return cls(
            connection_pool=pool,
            connection_params=connection_params,
            encryption_key=encryption_key,
        )
    
    @classmethod
    def from_yaml_config(
        cls,
        config: Dict[str, Any],
        encryption_key: Optional[str] = None,
    ) -> 'PostgresServiceFactory':
        """
        Deprecated: create factory from archi YAML config structure.
        Kept for startup ingest; runtime should use from_env/from_config.
        """
        db_config = config.get('database', {}).get('postgres', {})
        if not db_config:
            # Fallback to legacy location
            db_config = config.get('services', {}).get('postgres', {})
        
        connection_params = {
            'host': db_config.get('host', 'localhost'),
            'port': db_config.get('port', 5432),
            'database': db_config.get('database', 'archi'),
            'user': db_config.get('user', 'postgres'),
            'password': db_config.get('password', ''),
        }
        
        pool_config = db_config.get('pool', {})
        
        return cls.from_config(
            connection_params=connection_params,
            pool_min_conn=pool_config.get('min_connections', 5),
            pool_max_conn=pool_config.get('max_connections', 20),
            encryption_key=encryption_key or db_config.get('encryption_key'),
        )

    @classmethod
    def from_env(
        cls,
        *,
        password_override: Optional[str] = None,
        encryption_key: Optional[str] = None,
        pool_min_conn: int = 5,
        pool_max_conn: int = 20,
    ) -> 'PostgresServiceFactory':
        """
        Create factory from environment variables (PGHOST, PGPORT, PGDATABASE, PGUSER, PG_PASSWORD).
        """
        # Support common Postgres env var names used by compose/k8s
        host = os.environ.get('PGHOST', os.environ.get('POSTGRES_HOST', 'localhost'))
        port = int(os.environ.get('PGPORT', os.environ.get('POSTGRES_PORT', 5432)))
        database = os.environ.get('PGDATABASE', os.environ.get('POSTGRES_DB', 'postgres'))
        user = os.environ.get('PGUSER', os.environ.get('POSTGRES_USER', 'archi'))
        password = password_override or os.environ.get('PG_PASSWORD') or os.environ.get('POSTGRES_PASSWORD', '')

        connection_params = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password,
        }
        return cls.from_config(
            connection_params=connection_params,
            pool_min_conn=pool_min_conn,
            pool_max_conn=pool_max_conn,
            encryption_key=encryption_key or os.environ.get('PG_ENCRYPTION_KEY'),
        )
    
    @classmethod
    def get_instance(cls) -> Optional['PostgresServiceFactory']:
        """Get the singleton instance if initialized."""
        return cls._instance
    
    @classmethod
    def set_instance(cls, factory: 'PostgresServiceFactory') -> None:
        """Set the singleton instance."""
        cls._instance = factory
    
    @property
    def connection_pool(self) -> ConnectionPool:
        """Get the connection pool."""
        if self._pool is None:
            if self._conn_params:
                self._pool = ConnectionPool(connection_params=self._conn_params)
            else:
                raise ValueError("No connection pool or params available")
        return self._pool
    
    @property
    def user_service(self) -> UserService:
        """Get UserService (lazy-initialized)."""
        if self._user_service is None:
            self._user_service = UserService(
                connection_pool=self.connection_pool,
                encryption_key=self._encryption_key,
            )
        return self._user_service
    
    @property
    def config_service(self) -> ConfigService:
        """Get ConfigService (lazy-initialized)."""
        if self._config_service is None:
            self._config_service = ConfigService(
                connection_pool=self.connection_pool,
            )
        return self._config_service
    
    @property
    def conversation_service(self) -> ConversationService:
        """Get ConversationService (lazy-initialized)."""
        if self._conversation_service is None:
            self._conversation_service = ConversationService(
                connection_pool=self.connection_pool,
            )
        return self._conversation_service
    
    @property
    def document_selection_service(self) -> DocumentSelectionService:
        """Get DocumentSelectionService (lazy-initialized)."""
        if self._document_selection_service is None:
            self._document_selection_service = DocumentSelectionService(
                connection_pool=self.connection_pool,
            )
        return self._document_selection_service

    @property
    def playbook_service(self) -> PlaybookService:
        """Get PlaybookService (lazy-initialized) for the user-authored playbook library."""
        if self._playbook_service is None:
            self._playbook_service = PlaybookService(
                connection_pool=self.connection_pool,
            )
        return self._playbook_service
    
    def close(self) -> None:
        """Close connection pool and cleanup resources."""
        if self._pool:
            self._pool.close()
            self._pool = None
        
        # Clear service references
        self._user_service = None
        self._config_service = None
        self._conversation_service = None
        self._document_selection_service = None
        self._playbook_service = None
    
    def __enter__(self) -> 'PostgresServiceFactory':
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - close pool."""
        self.close()


# Convenience function for quick setup
def create_services(
    host: str = 'localhost',
    port: int = 5432,
    database: str = 'archi',
    user: str = 'postgres',
    password: str = '',
    encryption_key: Optional[str] = None,
) -> PostgresServiceFactory:
    """
    Quick factory creation with explicit parameters.
    
    Args:
        host: PostgreSQL host
        port: PostgreSQL port
        database: Database name
        user: Database user
        password: Database password
        encryption_key: BYOK encryption key
        
    Returns:
        Configured PostgresServiceFactory
    """
    return PostgresServiceFactory.from_config(
        connection_params={
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password,
        },
        encryption_key=encryption_key,
    )
