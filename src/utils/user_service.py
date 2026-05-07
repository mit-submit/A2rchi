"""
UserService - Manages user identity, preferences, and BYOK API keys.

Implements the Users Table requirements from the consolidate-to-postgres spec:
- User creation on first interaction
- Anonymous user identification
- BYOK API key storage (encrypted with pgcrypto)
- User preferences persistence
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Supported auth providers
AUTH_PROVIDERS = ("anonymous", "basic", "sso")

# Supported API key providers for BYOK
BYOK_PROVIDERS = ("openrouter", "openai", "anthropic")


@dataclass
class User:
    """User data model."""
    
    id: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    auth_provider: str = "anonymous"
    
    # Preferences
    theme: str = "system"
    preferred_model: Optional[str] = None
    preferred_temperature: Optional[float] = None
    ab_participation_rate: Optional[float] = None

    # API key presence flags
    api_key_openrouter: Optional[bool] = None
    api_key_openai: Optional[bool] = None
    api_key_anthropic: Optional[bool] = None
    
    # BYOK API keys (decrypted values, only populated when explicitly requested)
    api_keys: Dict[str, Optional[str]] = field(default_factory=dict)
    
    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserService:
    """
    Service for managing user data in PostgreSQL.
    
    Handles user creation, preferences, and encrypted BYOK API key storage.
    Uses pgcrypto for symmetric encryption of API keys.
    
    Example:
        >>> service = UserService(pg_config={'host': 'localhost', ...})
        >>> user = service.get_or_create_user("client_123")
        >>> service.update_preferences("client_123", theme="dark", preferred_model="gpt-4")
        >>> service.set_api_key("client_123", "openai", "sk-...")
    """
    
    def __init__(
        self,
        pg_config: Optional[Dict[str, Any]] = None,
        *,
        connection_pool=None,
        encryption_key: Optional[str] = None,
    ):
        """
        Initialize UserService.
        
        Args:
            pg_config: PostgreSQL connection parameters (fallback)
            connection_pool: ConnectionPool instance (preferred)
            encryption_key: Key for encrypting BYOK API keys (from BYOK_ENCRYPTION_KEY env)
        """
        self._pool = connection_pool
        self._pg_config = pg_config
        self._encryption_key = encryption_key or read_secret("BYOK_ENCRYPTION_KEY", default="")
        self._ensure_schema()
        
        if not self._encryption_key:
            logger.warning(
                "BYOK_ENCRYPTION_KEY not set - API key storage will be disabled"
            )
    
    def _get_connection(self) -> psycopg2.extensions.connection:
        """Get a database connection."""
        if self._pool:
            if hasattr(self._pool, "get_connection_direct"):
                return self._pool.get_connection_direct()
            return self._pool.get_connection()
        elif self._pg_config:
            return psycopg2.connect(**self._pg_config)
        else:
            raise ValueError("No connection pool or pg_config provided")
    
    def _release_connection(self, conn) -> None:
        """Release connection back to pool or close it."""
        if self._pool and hasattr(self._pool, "release_connection"):
            self._pool.release_connection(conn)
        else:
            conn.close()

    def _ensure_schema(self) -> None:
        """Create/extend user preference columns required by runtime features."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS ab_participation_rate NUMERIC(3,2)
                    """
                )
                conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.debug("Could not ensure users schema extensions: %s", exc)
        finally:
            self._release_connection(conn)

    @staticmethod
    def _row_to_user(row) -> User:
        return User(
            id=row["id"],
            display_name=row["display_name"],
            email=row["email"],
            auth_provider=row["auth_provider"],
            theme=row["theme"],
            preferred_model=row["preferred_model"],
            preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] is not None else None,
            ab_participation_rate=float(row["ab_participation_rate"]) if row.get("ab_participation_rate") is not None else None,
            api_key_openrouter=bool(row.get("api_key_openrouter")) if row.get("api_key_openrouter") is not None else False,
            api_key_openai=bool(row.get("api_key_openai")) if row.get("api_key_openai") is not None else False,
            api_key_anthropic=bool(row.get("api_key_anthropic")) if row.get("api_key_anthropic") is not None else False,
            created_at=str(row["created_at"]) if row["created_at"] else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        )
    
    def get_user(self, user_id: str) -> Optional[User]:
        """
        Get user by ID.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            User object if found, None otherwise
        """
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, display_name, email, auth_provider,
                           theme, preferred_model, preferred_temperature, ab_participation_rate,
                           (api_key_openrouter IS NOT NULL) AS api_key_openrouter,
                           (api_key_openai IS NOT NULL) AS api_key_openai,
                           (api_key_anthropic IS NOT NULL) AS api_key_anthropic,
                           created_at, updated_at
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,)
                )
                row = cursor.fetchone()
                
                if row is None:
                    return None

                return self._row_to_user(row)
        finally:
            self._release_connection(conn)
    
    def get_or_create_user(
        self,
        user_id: Optional[str] = None,
        *,
        auth_provider: str = "anonymous",
        display_name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> User:
        """
        Get existing user or create new one.
        
        Implements:
        - User creation on first interaction
        - Anonymous user identification (generates user_id if not provided)
        
        Args:
            user_id: User ID (generated if None for anonymous users)
            auth_provider: Authentication method ('anonymous', 'basic', 'sso')
            display_name: Optional display name
            email: Optional email address
            
        Returns:
            User object (existing or newly created)
        """
        if auth_provider not in AUTH_PROVIDERS:
            raise ValueError(f"auth_provider must be one of {AUTH_PROVIDERS}")
        
        # Generate user_id for anonymous users
        if user_id is None:
            user_id = f"anon_{uuid.uuid4().hex[:16]}"
        
        # Check if user exists
        existing = self.get_user(user_id)
        if existing is not None:
            return existing
        
        # Create new user
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO users (id, display_name, email, auth_provider)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                        email = COALESCE(EXCLUDED.email, users.email),
                        updated_at = NOW()
                    RETURNING id, display_name, email, auth_provider, theme,
                              preferred_model, preferred_temperature, ab_participation_rate,
                              (api_key_openrouter IS NOT NULL) AS api_key_openrouter,
                              (api_key_openai IS NOT NULL) AS api_key_openai,
                              (api_key_anthropic IS NOT NULL) AS api_key_anthropic,
                              created_at, updated_at
                    """,
                    (user_id, display_name, email, auth_provider)
                )
                row = cursor.fetchone()
                conn.commit()
                
                logger.info(f"Created/updated user: {user_id} (auth={auth_provider})")
                
                return self._row_to_user(row)
        finally:
            self._release_connection(conn)
    
    def update_preferences(
        self,
        user_id: str,
        *,
        display_name: Optional[str] = None,
        theme: Optional[str] = None,
        preferred_model: Optional[str] = None,
        preferred_temperature: Optional[float] = None,
        ab_participation_rate: Optional[float] = None,
    ) -> User:
        """
        Update user preferences.
        
        Implements: User preferences persistence
        
        Args:
            user_id: User ID
            display_name: Optional display name override
            theme: UI theme preference ('system', 'light', 'dark')
            preferred_model: Preferred model identifier
            preferred_temperature: Preferred temperature setting
            ab_participation_rate: Per-user A/B sampling override in the range 0..1
            
        Returns:
            Updated User object
            
        Raises:
            ValueError: If user not found
        """
        updates = []
        params: List[Any] = []

        if display_name is not None:
            updates.append("display_name = %s")
            params.append(display_name)
        
        if theme is not None:
            updates.append("theme = %s")
            params.append(theme)
        
        if preferred_model is not None:
            updates.append("preferred_model = %s")
            params.append(preferred_model)
        
        if preferred_temperature is not None:
            updates.append("preferred_temperature = %s")
            params.append(preferred_temperature)

        if ab_participation_rate is not None:
            updates.append("ab_participation_rate = %s")
            params.append(ab_participation_rate)
        
        if not updates:
            # No updates, just return current user
            user = self.get_user(user_id)
            if user is None:
                raise ValueError(f"User not found: {user_id}")
            return user
        
        updates.append("updated_at = NOW()")
        params.append(user_id)
        
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    f"""
                    UPDATE users
                    SET {', '.join(updates)}
                    WHERE id = %s
                    RETURNING id, display_name, email, auth_provider, theme,
                              preferred_model, preferred_temperature, ab_participation_rate,
                              (api_key_openrouter IS NOT NULL) AS api_key_openrouter,
                              (api_key_openai IS NOT NULL) AS api_key_openai,
                              (api_key_anthropic IS NOT NULL) AS api_key_anthropic,
                              created_at, updated_at
                    """,
                    params
                )
                row = cursor.fetchone()
                conn.commit()
                
                if row is None:
                    raise ValueError(f"User not found: {user_id}")
                
                logger.debug(f"Updated preferences for user: {user_id}")
                
                return self._row_to_user(row)
        finally:
            self._release_connection(conn)
    
    def set_api_key(
        self,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> bool:
        """
        Store encrypted BYOK API key for a user.
        
        Implements: BYOK API key storage with pgcrypto encryption
        
        Args:
            user_id: User ID
            provider: API provider ('openrouter', 'openai', 'anthropic')
            api_key: The API key to encrypt and store
            
        Returns:
            True if successful
            
        Raises:
            ValueError: If provider invalid or encryption key not configured
        """
        if provider not in BYOK_PROVIDERS:
            raise ValueError(f"provider must be one of {BYOK_PROVIDERS}")
        
        if not self._encryption_key:
            raise ValueError("BYOK_ENCRYPTION_KEY not configured - cannot store API keys")
        
        column = f"api_key_{provider}"
        
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # Use pgcrypto pgp_sym_encrypt for encryption
                cursor.execute(
                    f"""
                    UPDATE users
                    SET {column} = pgp_sym_encrypt(%s, %s),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (api_key, self._encryption_key, user_id)
                )
                conn.commit()
                
                if cursor.rowcount == 0:
                    raise ValueError(f"User not found: {user_id}")
                
                logger.info(f"Stored encrypted API key for user {user_id}, provider {provider}")
                return True
        finally:
            self._release_connection(conn)
    
    def get_api_key(
        self,
        user_id: str,
        provider: str,
    ) -> Optional[str]:
        """
        Retrieve and decrypt BYOK API key for a user.
        
        Args:
            user_id: User ID
            provider: API provider ('openrouter', 'openai', 'anthropic')
            
        Returns:
            Decrypted API key, or None if not set
            
        Raises:
            ValueError: If provider invalid or encryption key not configured
        """
        if provider not in BYOK_PROVIDERS:
            raise ValueError(f"provider must be one of {BYOK_PROVIDERS}")
        
        if not self._encryption_key:
            raise ValueError("BYOK_ENCRYPTION_KEY not configured - cannot retrieve API keys")
        
        column = f"api_key_{provider}"
        
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # Use pgcrypto pgp_sym_decrypt for decryption
                cursor.execute(
                    f"""
                    SELECT pgp_sym_decrypt({column}, %s) as decrypted_key
                    FROM users
                    WHERE id = %s AND {column} IS NOT NULL
                    """,
                    (self._encryption_key, user_id)
                )
                row = cursor.fetchone()
                
                if row is None:
                    return None
                
                # pgp_sym_decrypt returns bytes, decode to string
                decrypted = row[0]
                if isinstance(decrypted, (bytes, memoryview)):
                    return decrypted.decode("utf-8") if decrypted else None
                return decrypted
        finally:
            self._release_connection(conn)
    
    def delete_api_key(
        self,
        user_id: str,
        provider: str,
    ) -> bool:
        """
        Remove a stored API key.
        
        Args:
            user_id: User ID
            provider: API provider ('openrouter', 'openai', 'anthropic')
            
        Returns:
            True if key was deleted
        """
        if provider not in BYOK_PROVIDERS:
            raise ValueError(f"provider must be one of {BYOK_PROVIDERS}")
        
        column = f"api_key_{provider}"
        
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE users
                    SET {column} = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (user_id,)
                )
                conn.commit()
                
                logger.info(f"Deleted API key for user {user_id}, provider {provider}")
                return cursor.rowcount > 0
        finally:
            self._release_connection(conn)
    
    def link_anonymous_to_authenticated(
        self,
        anonymous_id: str,
        authenticated_id: str,
        *,
        auth_provider: str,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> User:
        """
        Link an anonymous user to an authenticated identity.
        
        Implements: Anonymous user can later be linked to an authenticated identity
        
        This migrates preferences and API keys from the anonymous user
        to the authenticated user, then deletes the anonymous record.
        
        Args:
            anonymous_id: The anonymous user ID to migrate from
            authenticated_id: The authenticated user ID to migrate to
            auth_provider: The authentication provider ('basic', 'sso')
            display_name: Display name for the authenticated user
            email: Email for the authenticated user
            
        Returns:
            The authenticated User object
        """
        if auth_provider == "anonymous":
            raise ValueError("Cannot link to anonymous auth provider")
        
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                # Get anonymous user data
                cursor.execute(
                    """
                    SELECT theme, preferred_model, preferred_temperature, ab_participation_rate,
                           api_key_openrouter, api_key_openai, api_key_anthropic
                    FROM users
                    WHERE id = %s AND auth_provider = 'anonymous'
                    """,
                    (anonymous_id,)
                )
                anon_data = cursor.fetchone()
                
                if anon_data is None:
                    logger.warning(f"Anonymous user not found: {anonymous_id}")
                    # Just create/return the authenticated user
                    return self.get_or_create_user(
                        authenticated_id,
                        auth_provider=auth_provider,
                        display_name=display_name,
                        email=email,
                    )
                
                # Create/update authenticated user with merged data
                cursor.execute(
                    """
                    INSERT INTO users (
                        id, display_name, email, auth_provider,
                        theme, preferred_model, preferred_temperature, ab_participation_rate,
                        api_key_openrouter, api_key_openai, api_key_anthropic
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                        email = COALESCE(EXCLUDED.email, users.email),
                        auth_provider = EXCLUDED.auth_provider,
                        theme = COALESCE(users.theme, EXCLUDED.theme),
                        preferred_model = COALESCE(users.preferred_model, EXCLUDED.preferred_model),
                        preferred_temperature = COALESCE(users.preferred_temperature, EXCLUDED.preferred_temperature),
                        ab_participation_rate = COALESCE(users.ab_participation_rate, EXCLUDED.ab_participation_rate),
                        api_key_openrouter = COALESCE(users.api_key_openrouter, EXCLUDED.api_key_openrouter),
                        api_key_openai = COALESCE(users.api_key_openai, EXCLUDED.api_key_openai),
                        api_key_anthropic = COALESCE(users.api_key_anthropic, EXCLUDED.api_key_anthropic),
                        updated_at = NOW()
                    RETURNING id, display_name, email, auth_provider, theme,
                              preferred_model, preferred_temperature, ab_participation_rate,
                              (api_key_openrouter IS NOT NULL) AS api_key_openrouter,
                              (api_key_openai IS NOT NULL) AS api_key_openai,
                              (api_key_anthropic IS NOT NULL) AS api_key_anthropic,
                              created_at, updated_at
                    """,
                    (
                        authenticated_id,
                        display_name,
                        email,
                        auth_provider,
                        anon_data["theme"],
                        anon_data["preferred_model"],
                        anon_data["preferred_temperature"],
                        anon_data["ab_participation_rate"],
                        anon_data["api_key_openrouter"],
                        anon_data["api_key_openai"],
                        anon_data["api_key_anthropic"],
                    )
                )
                row = cursor.fetchone()
                
                # Update conversation_metadata to point to new user
                cursor.execute(
                    """
                    UPDATE conversation_metadata
                    SET client_id = %s
                    WHERE client_id = %s
                    """,
                    (authenticated_id, anonymous_id)
                )
                
                # Update user_document_defaults to point to new user
                cursor.execute(
                    """
                    UPDATE user_document_defaults
                    SET user_id = %s
                    WHERE user_id = %s
                    ON CONFLICT (user_id, document_id) DO NOTHING
                    """,
                    (authenticated_id, anonymous_id)
                )
                
                # Delete anonymous user
                cursor.execute(
                    "DELETE FROM users WHERE id = %s",
                    (anonymous_id,)
                )
                
                conn.commit()
                
                logger.info(
                    f"Linked anonymous user {anonymous_id} to authenticated user {authenticated_id}"
                )
                
                return self._row_to_user(row)
        finally:
            self._release_connection(conn)
    
    def list_users(
        self,
        *,
        auth_provider: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[User]:
        """
        List users with optional filtering.
        
        Args:
            auth_provider: Filter by auth provider
            limit: Maximum results to return
            offset: Offset for pagination
            
        Returns:
            List of User objects
        """
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if auth_provider:
                    cursor.execute(
                        """
                        SELECT id, display_name, email, auth_provider,
                               theme, preferred_model, preferred_temperature, ab_participation_rate,
                               (api_key_openrouter IS NOT NULL) AS api_key_openrouter,
                               (api_key_openai IS NOT NULL) AS api_key_openai,
                               (api_key_anthropic IS NOT NULL) AS api_key_anthropic,
                               created_at, updated_at
                        FROM users
                        WHERE auth_provider = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        (auth_provider, limit, offset)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id, display_name, email, auth_provider,
                               theme, preferred_model, preferred_temperature, ab_participation_rate,
                               (api_key_openrouter IS NOT NULL) AS api_key_openrouter,
                               (api_key_openai IS NOT NULL) AS api_key_openai,
                               (api_key_anthropic IS NOT NULL) AS api_key_anthropic,
                               created_at, updated_at
                        FROM users
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        (limit, offset)
                    )
                
                rows = cursor.fetchall()
                
                return [self._row_to_user(row) for row in rows]
        finally:
            self._release_connection(conn)
