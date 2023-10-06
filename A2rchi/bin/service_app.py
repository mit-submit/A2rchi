#!/bin/python
from A2rchi.app.app import FlaskAppWrapper
from A2rchi.app.user import User
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret

from flask import Flask
from flask_login import LoginManager

import os
import sqlite3

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
config = Config_Loader().config["interfaces"]["app"]
global_config = Config_Loader().config["global"]

# database setup
print(f"Initializing database")
DB_PATH = os.path.join(global_config['DATA_PATH'], "flask_sqlite_db")

# read sql script
sql_script = None
with open(global_config['DB_INIT_SCRIPT'], 'r') as f:
    sql_script = f.read()

# connect to db, create user table if it doesn't exist, and commit
db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
cursor = db.cursor()
cursor.executescript(sql_script)
db.commit()
cursor.close()
db.close()


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

# fill in template variables for front-end JS
generate_script(config)

# initialize app object
print("Initializing flask app")
app = Flask(
    __name__,
    template_folder=config["template_folder"],
    static_folder=config["static_folder"],
)

# User session management setup: https://flask-login.readthedocs.io/en/latest
print("Setting up login manager")
login_manager = LoginManager()
login_manager.init_app(app)

# Flask-Login helper to retrieve a user from our db
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# start app
print(f"Starting App with (host, port): ({config['HOST']}, {config['PORT']})")
app = FlaskAppWrapper(app)
app.run(debug=True, port=config["PORT"], host=config["HOST"], ssl_context="adhoc")
