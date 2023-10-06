#!/bin/python
# from A2rchi.app.app import FlaskAppWrapper
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret

from flask import Flask

import os

# set openai
os.environ['OPENAI_API_KEY'] = os.environ['OPENAI_API_KEY'] # read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = os.environ['HF_TOKEN'] # read_secret("HUGGING_FACE_HUB_TOKEN")
config = Config_Loader().config["interfaces"]["chat_app"]
print(f"Starting App with (host, port): ({config['HOST']}, {config['PORT']})")

def generate_script(config):
    """
    This is not elegant but it creates the javascript file from the template using the config.yaml parameters
    """
    script_template = os.path.join(config["static_folder"], "script.js-template")
    with open(script_template, "r") as f:
        template = f.read()

    filled_template = template.replace('XX-HTTP_PORT-XX', str(config["PORT"]))

    script_file = os.path.join(config["static_folder"], "script.js")
    with open(script_file, "w") as f:
        f.write(filled_template)

    return

generate_script(config)
app = FlaskAppWrapper(Flask(
    __name__,
    template_folder=config["template_folder"],
    static_folder=config["static_folder"],
))
app.run(debug=True, port=config["PORT"], host=config["HOST"])



app = FlaskAppWrapper(Flask(
    __name__,
    template_folder="A2rchi/app/templates",
    static_folder="A2rchi/app/static",
))
app.run(debug=True, port=5000, host="localhost", ssl_context="adhoc")
