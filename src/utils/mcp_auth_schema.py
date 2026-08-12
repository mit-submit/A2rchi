"""
Canonical DDL for the per-user MCP OAuth *client* tables.

archi acts as an OAuth2 client to external MCP servers that require per-user
authorization (config: ``sso_auth: true``). Two tables back that flow:
  * ``mcp_oauth_clients`` — archi's own dynamic-client registration at each
    external server (one row per server), and
  * ``mcp_oauth_tokens``  — the per-user, per-server access/refresh tokens the
    external server issued, encrypted at rest with pgcrypto.

Single source consumed by BOTH schema paths so fresh and upgraded deployments
cannot drift:
  * the CLI's init.sql template (fresh deployments) renders it in via
    templates_manager, and
  * ConfigService._ensure_config_tables (existing deployments) executes it at
    startup.

Every statement is idempotent (IF NOT EXISTS), so running it on either path any
number of times is safe. Requires the ``users`` table to exist first.
"""

MCP_AUTH_TABLES_SQL = """
-- Encrypted token columns use pgcrypto's pgp_sym_encrypt/pgp_sym_decrypt.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- archi's OAuth2 client registration at each external MCP server (RFC 7591).
-- One row per external server archi connects to with per-user auth.
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

-- Per-user, per-server tokens issued by an external MCP server, encrypted at
-- rest (pgp_sym_encrypt with the deployment token-encryption key).
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
"""
