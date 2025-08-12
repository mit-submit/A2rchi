#!/bin/python
from a2rchi.interfaces.uploader_app.app import FlaskAppWrapper
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import setup_logging

from flask import Flask

import os

# set basicConfig for logging
setup_logging()

# set openai
os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

data_manager_config = load_config()["utils"]["data_manager"]
uploader_config = load_config()["interfaces"]["uploader_app"]

# Decision whether or not to run the vectorstore as a dynamic service or a static one
#
# - If http database is enabled, then the service can run dynamically. Thus the
#   vectorstore is constantly updated and the uploader app is run
#
# - If http database is not enabled, then the service cannot run
#   dynamically. Thus the vectorstore is only updated once and the uploader app
#   is not run.

run_dynamically = data_manager_config["use_HTTP_chromadb_client"]

if run_dynamically:
    app = FlaskAppWrapper(Flask(__name__, template_folder=uploader_config["template_folder"]))
    app.run(debug=uploader_config["flask_debug_mode"], port=uploader_config["PORT"], host=uploader_config["HOST"])