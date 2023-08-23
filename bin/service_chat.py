#!/bin/python
from interfaces.chat_app.app import FlaskAppWrapper
from flask import Flask
from threading import Thread

from config_loader import Config_Loader
config = Config_Loader().config["interfaces"]["chat_app"]

app = FlaskAppWrapper(Flask(__name__, template_folder = "../interfaces/chat_app/templates", static_folder="../interfaces/chat_app/static"))
app.run(debug=True, port=config["PORT"], host=config["HOST"])
