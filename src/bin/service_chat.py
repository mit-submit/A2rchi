#!/bin/python
import multiprocessing as mp
import os

from flask import Flask

from src.interfaces.chat_app.app import FlaskAppWrapper
from src.utils.env import read_secret
from src.utils.logging import setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.config_access import get_full_config


def main():
    
    setup_logging()

    # load secrets
    os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
    os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
    os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

    # Resolve Rucio values from *_FILE secrets so MCP child processes inherit concrete env vars.
    for key in [
        "RUCIO_HOST",
        "RUCIO_AUTH_HOST",
        "RUCIO_ACCOUNT",
        "RUCIO_AUTH_TYPE",
        "RUCIO_TIMEOUT",
        "RUCIO_VO",
        "RUCIO_CA_CERT",
        "RUCIO_CLIENT_CERT",
        "RUCIO_CLIENT_KEY",
        "RUCIO_CLIENT_PROXY",
        "X509_USER_PROXY",
        "RUCIO_CREDS_JSON",
    ]:
        value = read_secret(key)
        if value:
            os.environ[key] = value

    # X509 proxy vars need special handling: the rucio client expects a *file path*
    # pointing to PEM content, not the PEM text itself.
    # The secret file may contain either:
    #   a) A path string (e.g. /tmp/x509up_u1000) — use that path directly
    #   b) Actual PEM content — use the secret file path itself
    for key in ["RUCIO_CLIENT_PROXY", "X509_USER_PROXY"]:
        value = os.environ.get(key, "")
        if value and os.path.isfile(value):
            # Already set to a valid file path (e.g. from read_secret or env)
            continue
        file_env = f"{key}_FILE"
        secret_path = os.getenv(file_env)
        if secret_path and os.path.isfile(secret_path) and os.path.getsize(secret_path) > 0:
            content = open(secret_path).read().strip()
            if os.path.isfile(content):
                # Secret contains a path to the actual proxy file
                os.environ[key] = content
            else:
                # Secret contains PEM content directly; point rucio at the secret file
                os.environ[key] = secret_path

    # Generate a minimal rucio.cfg so the Rucio client library doesn't raise
    # ConfigNotFound.  All actual connection params are passed programmatically by
    # rucio-mcp, but the library unconditionally requires a config file to exist.
    _generate_rucio_cfg()

    # Set up shared Postgres services (expects config already in DB)
    factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
    PostgresServiceFactory.set_instance(factory)

    # Reload config from Postgres (runtime source of truth)
    config = get_full_config()
    chat_config = config["services"]["chat_app"]
    print(f"Starting Chat Service with (host, port): ({chat_config['host']}, {chat_config['port']})")
    print(f"Accessible externally at (host, port): ({chat_config['hostname']}, {chat_config['external_port']})")

    generate_script(chat_config)
    app = FlaskAppWrapper(Flask(
        __name__,
        template_folder=chat_config["template_folder"],
        static_folder=chat_config["static_folder"],
    ))
    app.run(debug=True, use_reloader=False, port=chat_config["port"], host=chat_config["host"])


def _generate_rucio_cfg():
    """Write a minimal rucio.cfg from env vars so the Rucio client library can load."""
    rucio_host = os.environ.get("RUCIO_HOST", "")
    if not rucio_host:
        return  # Rucio not configured; skip

    import configparser
    cfg = configparser.ConfigParser()
    cfg["client"] = {
        "rucio_host": rucio_host,
        "auth_host": os.environ.get("RUCIO_AUTH_HOST", ""),
        "account": os.environ.get("RUCIO_ACCOUNT", ""),
        "auth_type": os.environ.get("RUCIO_AUTH_TYPE", "x509_proxy"),
    }
    ca_cert = os.environ.get("RUCIO_CA_CERT", "")
    if ca_cert:
        cfg["client"]["ca_cert"] = ca_cert
    proxy = os.environ.get("X509_USER_PROXY", "") or os.environ.get("RUCIO_CLIENT_PROXY", "")
    if proxy:
        cfg["client"]["client_x509_proxy"] = proxy

    cfg_path = "/tmp/rucio.cfg"
    with open(cfg_path, "w") as f:
        cfg.write(f)
    os.environ["RUCIO_CONFIG"] = cfg_path
    print(f"Generated Rucio config at {cfg_path}")


def generate_script(chat_config):
    """
    This is not elegant but it creates the javascript file from the template using the config.yaml parameters
    """
    script_template = os.path.join(chat_config["static_folder"], "script.js-template")
    with open(script_template, "r") as f:
        template = f.read()

    filled_template = template.replace('XX-NUM-RESPONSES-XX', str(chat_config["num_responses_until_feedback"]))
    filled_template = filled_template.replace('XX-TRAINED_ON-XX', str(chat_config.get("trained_on", "")))

    script_file = os.path.join(chat_config["static_folder"], "script.js")
    with open(script_file, "w") as f:
        f.write(filled_template)

    return

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
