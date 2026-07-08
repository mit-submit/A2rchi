"""
Canonical DDL for the MCP/SSO auth tables.

Single source consumed by BOTH schema paths so fresh and upgraded
deployments cannot drift:
  * the CLI's init.sql template (fresh deployments) renders it in via
    templates_manager, and
  * ConfigService._ensure_config_tables (existing deployments) executes it
    at startup.

Every statement is idempotent (IF NOT EXISTS), so running it on either path
any number of times is safe. Requires the `users` table to exist first.
"""

MCP_AUTH_TABLES_SQL = """
-- SSO access/refresh tokens for chat-app users (encrypted at rest)
CREATE TABLE IF NOT EXISTS sso_tokens (
    user_id                 VARCHAR(200) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token            BYTEA,        -- pgp_sym_encrypt(token, BYOK_ENCRYPTION_KEY)
    refresh_token           BYTEA,        -- pgp_sym_encrypt(token, BYOK_ENCRYPTION_KEY)
    access_token_expires_at TIMESTAMPTZ,
    session_expires_at      TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- OAuth2 client registrations for MCP servers (one row per MCP server)
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
    server_name             VARCHAR(200) PRIMARY KEY,
    server_url              TEXT NOT NULL,
    client_id               TEXT NOT NULL,
    client_secret           TEXT NOT NULL DEFAULT '',
    redirect_uri            TEXT NOT NULL,
    auth_meta               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Per-user per-server MCP OAuth2 tokens
CREATE TABLE IF NOT EXISTS mcp_oauth_tokens (
    user_id                 VARCHAR(200) REFERENCES users(id) ON DELETE CASCADE,
    server_name             VARCHAR(200) NOT NULL,
    access_token            BYTEA,        -- pgp_sym_encrypt(token, encryption_key)
    refresh_token           BYTEA,        -- pgp_sym_encrypt(token, encryption_key)
    access_token_expires_at TIMESTAMPTZ,
    session_expires_at      TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, server_name)
);

-- Long-lived bearer tokens for the built-in MCP server (VS Code / Cursor)
CREATE TABLE IF NOT EXISTS mcp_tokens (
    token VARCHAR(64) PRIMARY KEY,        -- secrets.token_hex(32)
    user_id VARCHAR(200) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT,                    -- e.g. "VS Code - work laptop"
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ                -- NULL = never expires
);

CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user ON mcp_tokens(user_id);

-- Short-lived authorization codes for the OAuth2 PKCE flow used by MCP clients.
CREATE TABLE IF NOT EXISTS mcp_auth_codes (
    code VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(200) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_challenge VARCHAR(128) NOT NULL,
    code_challenge_method VARCHAR(10) NOT NULL DEFAULT 'S256',
    redirect_uri TEXT NOT NULL,
    client_id VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '10 minutes',
    used BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_mcp_auth_codes_expires ON mcp_auth_codes(expires_at);

-- OAuth2 dynamic client registrations (RFC 7591) used by MCP clients.
-- NOTE: distinct from mcp_oauth_clients above, which stores *our* client
-- registrations against external sso_auth MCP servers.
CREATE TABLE IF NOT EXISTS mcp_registered_clients (
    client_id VARCHAR(32) PRIMARY KEY,    -- secrets.token_hex(16)
    client_name TEXT,
    redirect_uris TEXT[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
