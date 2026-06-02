from flask import Flask, session
from unittest.mock import patch

from src.interfaces.chat_app.app import FlaskAppWrapper


def test_basic_auth_login_sets_identity_without_rbac_roles():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.add_url_rule("/chat", "index", lambda: "ok")

    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = app
    wrapper.salt = "salt"
    wrapper.sso_enabled = False
    wrapper.basic_auth_enabled = True
    wrapper.app.config["ACCOUNTS_FOLDER"] = "/tmp/test-accounts"

    with app.test_request_context("/login", method="POST", data={"username": "alice", "password": "secret"}):
        with patch("src.interfaces.chat_app.app.check_credentials", return_value=True):
            response = FlaskAppWrapper.login(wrapper)

        assert response.status_code == 302
        assert session["auth_method"] == "basic"
        assert session["user"]["username"] == "alice"
        assert session["roles"] == []
