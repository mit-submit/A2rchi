#!/bin/python
from a2rchi.interfaces.grader_app.app import FlaskAppWrapper
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import setup_logging

from flask import Flask

import os

# set basicConfig for logging and get debug value for flask app
setup_logging()

os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
config = Config_Loader().config["interfaces"]["grader_app"]

app = FlaskAppWrapper(Flask(
    __name__,
    template_folder=config["template_folder"],
))

app.run(debug=True, use_reloader=False, port=config["PORT"], host=config["HOST"])