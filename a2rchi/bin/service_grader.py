#!/bin/python
from a2rchi.interfaces.grader_app.app import FlaskAppWrapper
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import setup_logging

from flask import Flask

import os

# set basicConfig for logging and get debug value for flask app
setup_logging()

os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
grader_config = load_config()["services"]["grader_app"]

app = FlaskAppWrapper(Flask(
    __name__,
    template_folder=grader_config["template_folder"],
))

app.run(debug=grader_config["flask_debug_mode"], use_reloader=False, port=grader_config["port"], host=grader_config["host"])