from A2rchi.interfaces.chat_app.app import FlaskAppWrapper
from A2rchi.interfaces.chat_app.user import User
from A2rchi.utils.config_loader import Config_Loader

from flask import Flask
from flask_login import LoginManager

global_config = Config_Loader().config["global"]
app_config = Config_Loader().config["interfaces"]["chat_app"]

import os
import sqlite3

EXTERNAL_PORT = 7555
HOSTNAME = "t3desk019.mit.edu"
HOST = "0.0.0.0"
NUM_RESPONSES_UNTIL_FEEDBACK = 100
TRAINED_ON_ = "FRONT END TEST"
STATIC_FOLDER = "/work/submit/juliush/A2rchi_test/A2rchi/A2rchi/interfaces/chat_app/static"
TEMPLATE_FOLDER = "/work/submit/juliush/A2rchi_test/A2rchi/A2rchi/interfaces/chat_app/templates"

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

def generate_script():
    """
    This is not elegant but it creates the javascript file from the template using the config.yaml parameters
    """
    script_template = os.path.join(STATIC_FOLDER, "script.js-template")
    with open(script_template, "r") as f:
        template = f.read()

    filled_template = template.replace('XX-HTTP_PORT-XX', str(EXTERNAL_PORT))
    filled_template = filled_template.replace('XX-HOSTNAME-XX', str(HOSTNAME))
    filled_template = filled_template.replace('XX-NUM-RESPONSES-XX', str(NUM_RESPONSES_UNTIL_FEEDBACK))
    filled_template = filled_template.replace('XX-TRAINED_ON-XX', str(TRAINED_ON_))

    script_file = os.path.join(STATIC_FOLDER, "script.js")
    with open(script_file, "w") as f:
        f.write(filled_template)

    return

# fill in template variables for front-end JS
generate_script()

# initialize app object
print("Initializing flask app")
app = Flask(
    __name__,
    template_folder=TEMPLATE_FOLDER,
    static_folder=STATIC_FOLDER,
)

# User session management setup: https://flask-login.readthedocs.io/en/latest
print("Setting up login manager")
login_manager = LoginManager()
login_manager.init_app(app)

# Flask-Login helper to retrieve a user from our db
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# load certificates: TODO: move this to config
certificate_path = "/work/submit/juliush/tem/a2rchi_mit_edu_cert.cer"
key_path = "/work/submit/juliush/tem/a2rchi-key.pem"

# start app
print(f"Starting App with (host, port): ({HOST}, {EXTERNAL_PORT})")
app = FlaskAppWrapper(app)
app.run(debug=True, port=EXTERNAL_PORT, host=HOST, ssl_context=(certificate_path, key_path))
