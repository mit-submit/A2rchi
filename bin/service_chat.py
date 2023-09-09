#!/bin/python
import sys
from interfaces.chat_app.app import FlaskAppWrapper
from flask import Flask
from threading import Thread

from config_loader import Config_Loader
config = Config_Loader().config["interfaces"]["chat_app"]

def generate_script(config):
    '''
    This is not elegant but it creates the javascript file from the template using the config.yaml parameters
    '''
    print(config["HOST"],config["PORT"])
    with open("./interfaces/chat_app/static/script.js-template","r") as f:
        data = f.read()
    data = data.replace('XX-HTTP_PORT-XX',str(config["PORT"]))
    with open("./interfaces/chat_app/static/script.js","w") as f:
        f.write(data)
    return
    
generate_script(config)
app = FlaskAppWrapper(Flask(__name__,
                            template_folder = "../interfaces/chat_app/templates",
                            static_folder = "../interfaces/chat_app/static"))
app.run(debug=True, port=config["PORT"], host=config["HOST"])
