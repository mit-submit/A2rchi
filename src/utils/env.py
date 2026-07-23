import os
import secrets


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