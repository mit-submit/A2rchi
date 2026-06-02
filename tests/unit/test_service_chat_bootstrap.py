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
