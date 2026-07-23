from unittest.mock import Mock, patch

from src.bin import service_chat


def test_service_chat_registers_shared_api_blueprint():
    fake_factory = Mock()
    wrapped_app = Mock()

    config = {
        "services": {
            "chat_app": {
                "host": "0.0.0.0",
                "port": 7861,
                "hostname": "localhost",
                "external_port": 7861,
                "template_folder": "/tmp/templates",
                "static_folder": "/tmp/static",
            }
        }
    }

    with patch("src.bin.service_chat.setup_logging"), \
         patch("src.bin.service_chat.read_secret", return_value="secret"), \
         patch("src.bin.service_chat.PostgresServiceFactory.from_env", return_value=fake_factory), \
         patch("src.bin.service_chat.PostgresServiceFactory.set_instance"), \
         patch("src.bin.service_chat.get_full_config", return_value=config), \
         patch("src.bin.service_chat.generate_script"), \
         patch("src.bin.service_chat.register_api") as register_api_mock, \
         patch("src.bin.service_chat.FlaskAppWrapper", return_value=wrapped_app) as wrapper_cls:
        service_chat.main()

    flask_app = wrapper_cls.call_args.args[0]
    register_api_mock.assert_called_once_with(flask_app)
    wrapped_app.run.assert_called_once()


_CONFIG = {
    "services": {
        "chat_app": {
            "host": "0.0.0.0",
            "port": 7861,
            "hostname": "localhost",
            "external_port": 7861,
            "template_folder": "/tmp/templates",
            "static_folder": "/tmp/static",
        }
    }
}


def test_service_chat_ensures_playbook_schema_before_app_build():
    """main() runs the playbook schema migration once, via the factory's
    PlaybookService, before the Flask app is constructed — it no longer happens
    inside ChatWrapper.__init__."""
    fake_factory = Mock()
    wrapped_app = Mock()
    calls = []

    fake_factory.playbook_service.ensure_schema.side_effect = lambda: calls.append("ensure_schema")

    def build_app(*args, **kwargs):
        calls.append("app_built")
        return wrapped_app

    with patch("src.bin.service_chat.setup_logging"), \
         patch("src.bin.service_chat.read_secret", return_value="secret"), \
         patch("src.bin.service_chat.PostgresServiceFactory.from_env", return_value=fake_factory), \
         patch("src.bin.service_chat.PostgresServiceFactory.set_instance"), \
         patch("src.bin.service_chat.get_full_config", return_value=_CONFIG), \
         patch("src.bin.service_chat.generate_script"), \
         patch("src.bin.service_chat.register_api"), \
         patch("src.bin.service_chat.FlaskAppWrapper", side_effect=build_app):
        service_chat.main()

    fake_factory.playbook_service.ensure_schema.assert_called_once()
    assert calls.index("ensure_schema") < calls.index("app_built"), (
        "playbook schema must be ensured before the Flask app is built"
    )


def test_service_chat_playbook_schema_failure_does_not_block_startup():
    """A failed playbook migration logs and continues: the chat flow tolerates a
    missing side table, so fail-fast here would take chat down for everyone."""
    fake_factory = Mock()
    wrapped_app = Mock()

    fake_factory.playbook_service.ensure_schema.side_effect = RuntimeError("db not ready")

    with patch("src.bin.service_chat.setup_logging"), \
         patch("src.bin.service_chat.read_secret", return_value="secret"), \
         patch("src.bin.service_chat.PostgresServiceFactory.from_env", return_value=fake_factory), \
         patch("src.bin.service_chat.PostgresServiceFactory.set_instance"), \
         patch("src.bin.service_chat.get_full_config", return_value=_CONFIG), \
         patch("src.bin.service_chat.generate_script"), \
         patch("src.bin.service_chat.register_api"), \
         patch("src.bin.service_chat.FlaskAppWrapper", return_value=wrapped_app):
        service_chat.main()  # must not raise

    fake_factory.playbook_service.ensure_schema.assert_called_once()
    wrapped_app.run.assert_called_once()
