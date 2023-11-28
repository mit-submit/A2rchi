#!/bin/python

from a2rchi.interfaces.chat_app.app import FlaskAppWrapper
from a2rchi.interfaces.chat_app.user import User
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.env import read_secret

from flask import Flask
from flask_login import LoginManager
import tempfile

global_config = Config_Loader().config["global"]
app_config = Config_Loader().config["interfaces"]["chat_app"]

import os
import sqlite3

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

# database setup
print(f"Initializing database")
DB_PATH = os.path.join(global_config['DATA_PATH'], "flask_sqlite_db")

# read sql script
sql_script = None
with open(app_config['DB_INIT_SCRIPT'], 'r') as f:
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
generate_script(app_config)

# initialize app object
print("Initializing flask app")
app = Flask(
    __name__,
    template_folder=app_config["template_folder"],
    static_folder=app_config["static_folder"],
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
print(f"Starting Chat Service with (host, port): ({app_config['HOST']}, {app_config['PORT']})")
app = FlaskAppWrapper(app)
if app_config["HOSTNAME"] == "a2rchi.mit.edu":

    print("Adding SSL certificates for a2rchi.mit.edu")

    #get the ssl cert and key and save them to temporary files
    ssl_cert = read_secret("A2RCHI_SSL_CERTIFICATE")
    ssl_key = read_secret("A2RCHI_SSL_CERTIFICATE_KEY")
    cert_file = tempfile.NamedTemporaryFile(delete=False)
    key_file = tempfile.NamedTemporaryFile(delete=False)
    cert_file.write(ssl_cert.encode())
    key_file.write(ssl_key.encode())

    app.run(debug=True, port=app_config["PORT"], host=app_config["HOST"], ssl_context=(cert_file.name, key_file.name))

    #remove the temp ssl cert and key temp files
    os.unlink(cert_file.name)
    os.unlink(key_file.name)

else:
    
    print("No SSL certificate for this server found. Starting up with adhoc SSL certification")
    app.run(debug=True, port=app_config["PORT"], host=app_config["HOST"], ssl_context="adhoc")