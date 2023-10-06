#!/bin/python
from A2rchi.interfaces.chat_app.app import FlaskAppWrapper
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret

from flask import Flask

import os

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
config = Config_Loader().config["interfaces"]["chat_app"]
global_config = Config_Loader().config["global"]
print(f"Starting Chat Service with (host, port): ({config['HOST']}, {config['PORT']})")

def generate_script(config):
    """
    This is not elegant but it creates the javascript file from the template using the config.yaml parameters
    """
    script_template = os.path.join(config["static_folder"], "script.js-template")
    with open(script_template, "r") as f:
        template = f.read()

    filled_template = template.replace('XX-HTTP_PORT-XX', str(config["EXTERNAL_PORT"]))
    filled_template = filled_template.replace('XX-HOSTNAME-XX', str(config["HOSTNAME"]))
    filled_template = filled_template.replace('XX-NUM-RESPONSES-XX', str(config["num_responses_until_feedback"]))
    filled_template = filled_template.replace('XX-TRAINED_ON-XX', str(global_config["TRAINED_ON"]))

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
