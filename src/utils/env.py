import logging
import os
import secrets
from functools import lru_cache

logger = logging.getLogger(__name__)


def read_secret(secret_name, default=""):
    """
    Read a secret from a file or environment variable.
    
    Args:
        secret_name: Name of the secret (e.g., 'POSTGRES_PASSWORD')
        default: Default value if secret is not found
        
    Returns:
        The secret value, or the default if not found
    """
    # fetch filepath from env variable
    secret_filepath = os.getenv(f"{secret_name}_FILE")

    if secret_filepath:
        # read secret from file and return
        with open(secret_filepath, 'r') as f:
            secret = f.read()
        return secret.strip()

    # fallback to direct environment variable if no *_FILE is set
    env_value = os.getenv(secret_name)
    if env_value:
        return env_value.strip()

    return default


@lru_cache(maxsize=1)
def get_token_encryption_key():
    """
    Resolve the pgcrypto key used to encrypt stored credentials at rest
    (BYOK API keys, SSO tokens, MCP OAuth tokens).

    Single resolution point so every store encrypts with the same key —
    stores that resolved different fallbacks would silently corrupt each
    other's rows. Cached for the process lifetime; secrets are immutable
    per deployment.

    Returns "" when no key is configured; callers must skip persistence.
    """
    key = read_secret("BYOK_ENCRYPTION_KEY")
    if key:
        return key
    fallback = (
        read_secret("PG_ENCRYPTION_KEY")
        or read_secret("UPLOADER_SALT")
        or read_secret("FLASK_UPLOADER_APP_SECRET_KEY")
    )
    if fallback:
        logger.warning(
            "BYOK_ENCRYPTION_KEY not set — falling back to another configured "
            "secret for token encryption. Set BYOK_ENCRYPTION_KEY explicitly; "
            "changing the effective key later makes previously stored tokens "
            "undecryptable."
        )
        return fallback
    logger.warning(
        "No token encryption key found (BYOK_ENCRYPTION_KEY / PG_ENCRYPTION_KEY / "
        "UPLOADER_SALT / FLASK_UPLOADER_APP_SECRET_KEY) — encrypted token stores "
        "are disabled and sso_auth MCP servers will be skipped."
    )
    return ""


def ssl_verify():
    """
    TLS verify value for HTTPS clients that may talk to internal services:
    the CA bundle from SSL_CERT_FILE (exported by the ssl_cert_file deploy
    option) or the conventional container mount path, else certifi (True).
    """
    for path in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/certs/tls-ca-bundle.pem"):
        if path and os.path.exists(path):
            return path
    return True

def read_or_create_persistent_secret(secret_name, persist_dir, filename=None):
    """Return a stable secret, persisting a generated one when none is configured.

    Resolution order:
      1. The configured secret (env var or ``*_FILE``) via :func:`read_secret`.
      2. A previously persisted random key at ``<persist_dir>/<filename>``
         (default ``.<secret_name lowercased>``).
      3. A freshly generated key, written to that path (mode 0600) for next time.

    Persisting matters for signing keys such as the Flask session secret: a fresh random key
    generated on every boot invalidates all signed sessions, logging every user out on each
    restart. Falls back to an ephemeral key if ``persist_dir`` is not writable.

    ``filename`` exists for apps that share both a persist_dir (mounted volume)
    and, historically, a secret name: each app persists its OWN key file, so an
    auto-generated signing key is never silently shared across apps.
    """
    configured = read_secret(secret_name)
    if configured:
        return configured

    path = os.path.join(persist_dir, filename or f".{secret_name.lower()}")
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                existing = f.read().strip()
            if existing:
                return existing
    except OSError:
        pass

    value = secrets.token_hex(32)
    try:
        os.makedirs(persist_dir, exist_ok=True)
        # 0600 must hold from creation — open()+chmod() leaves a window where the
        # key is world-readable; fchmod covers a pre-existing wider-mode file,
        # which os.open's mode argument does not touch.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            os.fchmod(fd, 0o600)
            f.write(value)
    except OSError:
        pass  # unwritable dir: fall back to an ephemeral key (still valid this process)
    return value
