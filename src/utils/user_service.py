"""
UserService - Manages user identity, preferences, and BYOK API keys.

Implements the Users Table requirements from the consolidate-to-postgres spec:
- User creation on first interaction
- Anonymous user identification
- BYOK API key storage (encrypted with pgcrypto)
- User preferences persistence
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.utils.rbac.audit import log_authentication_event

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
    is_admin: bool = False

    # Preferences
    theme: str = "system"
    preferred_model: Optional[str] = None
    preferred_temperature: Optional[float] = None
    
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
        
        if not self._encryption_key:
            logger.warning(
                "BYOK_ENCRYPTION_KEY not set - API key storage will be disabled"
            )
    
    def _get_connection(self) -> psycopg2.extensions.connection:
        """Get a database connection."""
        if self._pool:
            return self._pool.get_connection()
        elif self._pg_config:
            return psycopg2.connect(**self._pg_config)
        else:
            raise ValueError("No connection pool or pg_config provided")
    
    def _release_connection(self, conn) -> None:
        """Release connection back to pool or close it."""
        if self._pool:
            self._pool.release_connection(conn)
        else:
            conn.close()
    
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
                    SELECT id, display_name, email, auth_provider, is_admin,
                           theme, preferred_model, preferred_temperature,
                           created_at, updated_at
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,)
                )
                row = cursor.fetchone()
                
                if row is None:
                    return None
                
                return User(
                    id=row["id"],
                    display_name=row["display_name"],
                    email=row["email"],
                    auth_provider=row["auth_provider"],
                    is_admin=row["is_admin"],
                    theme=row["theme"],
                    preferred_model=row["preferred_model"],
                    preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] else None,
                    created_at=str(row["created_at"]) if row["created_at"] else None,
                    updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                )
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
                    RETURNING id, display_name, email, auth_provider, is_admin, theme,
                              preferred_model, preferred_temperature, created_at, updated_at
                    """,
                    (user_id, display_name, email, auth_provider)
                )
                row = cursor.fetchone()
                conn.commit()

                logger.info(f"Created/updated user: {user_id} (auth={auth_provider})")

                return User(
                    id=row["id"],
                    display_name=row["display_name"],
                    email=row["email"],
                    auth_provider=row["auth_provider"],
                    is_admin=row["is_admin"],
                    theme=row["theme"],
                    preferred_model=row["preferred_model"],
                    preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] else None,
                    created_at=str(row["created_at"]) if row["created_at"] else None,
                    updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                )
        finally:
            self._release_connection(conn)
    
    def update_preferences(
        self,
        user_id: str,
        *,
        theme: Optional[str] = None,
        preferred_model: Optional[str] = None,
        preferred_temperature: Optional[float] = None,
    ) -> User:
        """
        Update user preferences.
        
        Implements: User preferences persistence
        
        Args:
            user_id: User ID
            theme: UI theme preference ('system', 'light', 'dark')
            preferred_model: Preferred model identifier
            preferred_temperature: Preferred temperature setting
            
        Returns:
            Updated User object
            
        Raises:
            ValueError: If user not found
        """
        updates = []
        params: List[Any] = []
        
        if theme is not None:
            updates.append("theme = %s")
            params.append(theme)
        
        if preferred_model is not None:
            updates.append("preferred_model = %s")
            params.append(preferred_model)
        
        if preferred_temperature is not None:
            updates.append("preferred_temperature = %s")
            params.append(preferred_temperature)
        
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
                              preferred_model, preferred_temperature, created_at, updated_at
                    """,
                    params
                )
                row = cursor.fetchone()
                conn.commit()
                
                if row is None:
                    raise ValueError(f"User not found: {user_id}")
                
                logger.debug(f"Updated preferences for user: {user_id}")
                
                return User(
                    id=row["id"],
                    display_name=row["display_name"],
                    email=row["email"],
                    auth_provider=row["auth_provider"],
                    theme=row["theme"],
                    preferred_model=row["preferred_model"],
                    preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] else None,
                    created_at=str(row["created_at"]) if row["created_at"] else None,
                    updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                )
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

    # ----- Multi-token API (admin-minted, named) -----

    def create_api_token(
        self,
        user_id: str,
        name: str,
        *,
        ttl_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a named API token for /v1 endpoint access.

        Inserts an `archi_<hex>` token into api_tokens and returns the
        plaintext once. The plaintext is never stored (only its SHA-256 hash).

        Args:
            user_id: Owner of the token (must exist in users).
            name: Token label, unique per user.
            ttl_days: Days until the token expires. None (or <=0) means
                never expires. The expiry is stored per-token in
                api_tokens.expires_at.

        Returns:
            {"id": <uuid>, "token": <plaintext>, "name": <name>,
             "expires_at": <iso str or None>}

        Raises:
            ValueError: If user not found, name is empty, or name already in use
                for this user (revoked tokens don't count — pick a new name).
        """
        if not name or not name.strip():
            raise ValueError("Token name must be non-empty")
        name = name.strip()

        token = f"archi_{secrets.token_hex(16)}"
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        expires_at = None
        if ttl_days is not None and ttl_days > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(days=int(ttl_days))

        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
                if cursor.fetchone() is None:
                    raise ValueError(f"User not found: {user_id}")

                try:
                    cursor.execute(
                        """
                        INSERT INTO api_tokens (user_id, name, token_hash, expires_at)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, expires_at
                        """,
                        (user_id, name, token_hash, expires_at),
                    )
                    row = cursor.fetchone()
                    token_id = str(row[0])
                    expires_iso = row[1].isoformat() if row[1] else None
                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    raise ValueError(
                        f"Token name '{name}' already in use for user {user_id}"
                    )

                logger.info(
                    "Created API token %s for user %s (name=%s, expires_at=%s)",
                    token_id, user_id, name, expires_iso,
                )
                log_authentication_event(user_id, "api_token_create", success=True, method="bearer_token")
                return {
                    "id": token_id,
                    "token": token,
                    "name": name,
                    "expires_at": expires_iso,
                }
        finally:
            self._release_connection(conn)

    def list_api_tokens(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List API tokens. Never returns plaintext or hashes.

        Args:
            user_id: If provided, only tokens for this user. Otherwise all tokens
                (admin-only caller is responsible for permission gating).

        Returns:
            List of {id, user_id, user_email, name, created_at, last_used_at,
            revoked_at, expires_at}.
        """
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if user_id is None:
                    cursor.execute(
                        """
                        SELECT t.id, t.user_id, u.email AS user_email, t.name,
                               t.created_at, t.last_used_at, t.revoked_at, t.expires_at
                        FROM api_tokens t
                        JOIN users u ON u.id = t.user_id
                        ORDER BY t.created_at DESC
                        """
                    )
                else:
                    cursor.execute(
                        """
                        SELECT t.id, t.user_id, u.email AS user_email, t.name,
                               t.created_at, t.last_used_at, t.revoked_at, t.expires_at
                        FROM api_tokens t
                        JOIN users u ON u.id = t.user_id
                        WHERE t.user_id = %s
                        ORDER BY t.created_at DESC
                        """,
                        (user_id,),
                    )
                return [
                    {
                        "id": str(row["id"]),
                        "user_id": row["user_id"],
                        "user_email": row["user_email"],
                        "name": row["name"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
                        "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                    }
                    for row in cursor.fetchall()
                ]
        finally:
            self._release_connection(conn)

    def revoke_api_token_by_id(self, token_id: str, *, user_id: Optional[str] = None) -> bool:
        """
        Soft-revoke a specific token by id.

        Args:
            token_id: UUID of the token to revoke.
            user_id: If provided, also require the token belong to this user
                (prevents cross-user revocation by guessing IDs).

        Returns:
            True if a token was revoked, False otherwise.
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                if user_id is None:
                    cursor.execute(
                        """
                        UPDATE api_tokens
                        SET revoked_at = NOW()
                        WHERE id = %s AND revoked_at IS NULL
                        RETURNING user_id
                        """,
                        (token_id,),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE api_tokens
                        SET revoked_at = NOW()
                        WHERE id = %s AND user_id = %s AND revoked_at IS NULL
                        RETURNING user_id
                        """,
                        (token_id, user_id),
                    )
                row = cursor.fetchone()
                conn.commit()
                if row is None:
                    return False
                log_authentication_event(row[0], "api_token_revoke", success=True, method="bearer_token")
                return True
        finally:
            self._release_connection(conn)

    def get_user_by_api_token(self, token: str, *, token_ttl_days: Optional[int] = None) -> Optional[User]:
        """
        Look up a user by their API token via the api_tokens table.

        Hashes the provided token and joins on token_hash for O(1) lookup.
        Updates last_used_at on successful match. Revoked tokens never match.

        Args:
            token: The plaintext API token.
            token_ttl_days: Fallback global TTL applied only to tokens with no
                per-token expires_at (legacy rows). When None, skip the
                fallback check. Tokens with expires_at set are governed
                entirely by that column.

        Returns:
            User object if token is valid/active/unexpired, None otherwise.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT u.id, u.display_name, u.email, u.auth_provider, u.is_admin,
                           u.theme, u.preferred_model, u.preferred_temperature,
                           u.created_at, u.updated_at,
                           t.id AS token_id, t.created_at AS token_created_at,
                           t.expires_at
                    FROM api_tokens t
                    JOIN users u ON u.id = t.user_id
                    WHERE t.token_hash = %s AND t.revoked_at IS NULL
                    """,
                    (token_hash,),
                )
                row = cursor.fetchone()

                if row is None:
                    return None

                now = datetime.now(timezone.utc)
                if row["expires_at"] is not None:
                    if now > row["expires_at"]:
                        logger.warning(
                            "Expired API token for user %s (expired_at: %s)",
                            row["id"], row["expires_at"],
                        )
                        return None
                elif token_ttl_days is not None and row["token_created_at"] is not None:
                    age = now - row["token_created_at"]
                    if age > timedelta(days=token_ttl_days):
                        logger.warning(
                            "Expired API token for user %s (age: %s, fallback ttl: %d days)",
                            row["id"], age, token_ttl_days,
                        )
                        return None

                cursor.execute(
                    "UPDATE api_tokens SET last_used_at = NOW() WHERE id = %s",
                    (row["token_id"],),
                )
                conn.commit()

                return User(
                    id=row["id"],
                    display_name=row["display_name"],
                    email=row["email"],
                    auth_provider=row["auth_provider"],
                    is_admin=row["is_admin"],
                    theme=row["theme"],
                    preferred_model=row["preferred_model"],
                    preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] else None,
                    created_at=str(row["created_at"]) if row["created_at"] else None,
                    updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                )
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
                    SELECT theme, preferred_model, preferred_temperature,
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
                        theme, preferred_model, preferred_temperature,
                        api_key_openrouter, api_key_openai, api_key_anthropic
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                        email = COALESCE(EXCLUDED.email, users.email),
                        auth_provider = EXCLUDED.auth_provider,
                        theme = COALESCE(users.theme, EXCLUDED.theme),
                        preferred_model = COALESCE(users.preferred_model, EXCLUDED.preferred_model),
                        preferred_temperature = COALESCE(users.preferred_temperature, EXCLUDED.preferred_temperature),
                        api_key_openrouter = COALESCE(users.api_key_openrouter, EXCLUDED.api_key_openrouter),
                        api_key_openai = COALESCE(users.api_key_openai, EXCLUDED.api_key_openai),
                        api_key_anthropic = COALESCE(users.api_key_anthropic, EXCLUDED.api_key_anthropic),
                        updated_at = NOW()
                    RETURNING id, display_name, email, auth_provider, theme,
                              preferred_model, preferred_temperature, created_at, updated_at
                    """,
                    (
                        authenticated_id,
                        display_name,
                        email,
                        auth_provider,
                        anon_data["theme"],
                        anon_data["preferred_model"],
                        anon_data["preferred_temperature"],
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
                
                return User(
                    id=row["id"],
                    display_name=row["display_name"],
                    email=row["email"],
                    auth_provider=row["auth_provider"],
                    theme=row["theme"],
                    preferred_model=row["preferred_model"],
                    preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] else None,
                    created_at=str(row["created_at"]) if row["created_at"] else None,
                    updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                )
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
                               theme, preferred_model, preferred_temperature,
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
                               theme, preferred_model, preferred_temperature,
                               created_at, updated_at
                        FROM users
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                        """,
                        (limit, offset)
                    )
                
                rows = cursor.fetchall()
                
                return [
                    User(
                        id=row["id"],
                        display_name=row["display_name"],
                        email=row["email"],
                        auth_provider=row["auth_provider"],
                        theme=row["theme"],
                        preferred_model=row["preferred_model"],
                        preferred_temperature=float(row["preferred_temperature"]) if row["preferred_temperature"] else None,
                        created_at=str(row["created_at"]) if row["created_at"] else None,
                        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                    )
                    for row in rows
                ]
        finally:
            self._release_connection(conn)
