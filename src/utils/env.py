import logging
import os
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