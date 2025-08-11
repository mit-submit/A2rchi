#!/bin/python
from a2rchi.interfaces.chat_app.app import FlaskAppWrapper
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import setup_logging

from flask import Flask

import os
import multiprocessing as mp


# set basicConfig for logging
setup_logging()

# set openai
def main():
    os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
    os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
    os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
    config = load_config()["interfaces"]["chat_app"]
    global_config = load_config()["global"]
    print(f"Starting Chat Service with (host, port): ({config['HOST']}, {config['PORT']})")
    print(f"Accessible externally at (host, port): ({config['HOSTNAME']}, {config['EXTERNAL_PORT']})")

    generate_script(config,global_config)
    app = FlaskAppWrapper(Flask(
        __name__,
        template_folder=config["template_folder"],
        static_folder=config["static_folder"],
    ))
    app.run(debug=True, use_reloader=False, port=config["PORT"], host=config["HOST"])


def generate_script(config,global_config):
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

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()

