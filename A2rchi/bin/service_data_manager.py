#!/bin/python
from A2rchi.interfaces.uploader_app.app import FlaskAppWrapper
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.data_manager import DataManager
from A2rchi.utils.env import read_secret
from A2rchi.utils.scraper import Scraper

from flask import Flask
from threading import Thread

import os
import time


# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

data_manager_config = Config_Loader().config["utils"]["data_manager"]
uploader_config = Config_Loader().config["interfaces"]["uploader_app"]

# Decision whether or not to run the vectorstore as a dynamic service or a static one
#
# - If http database is enabled, then the service can run dynamically. Thus the
#   vectorstore is constantly updated and the uploader app is run
#
# - If http database is not enabled, then the service cannot run
#   dynamically. Thus the vectorstore is only updated once and the uploader app
#   is not run.

run_dynamically = data_manager_config["use_HTTP_chromadb_client"]
print(f" Dynamic: {run_dynamically}")

# scrape data onto the filesystem
scraper = Scraper()
scraper.hard_scrape(verbose=True)

def run_data_manager():
    """
    function which runs the data manager
    """
    data_manager = DataManager()
    stop = False
    while not stop:

        # check to see if this function should only be run once or should be run indefinitely
        if not run_dynamically:
            stop = True 

        # do updating of vectorstore
        print("Starting update vectorstore")
        data_manager.update_vectorstore()

        print(f"Completed vectorstore update (sleep {data_manager_config['vectordb_update_time']} seconds)\n")
        time.sleep(int(data_manager_config["vectordb_update_time"]))

    return

data_manager_thread = Thread(target=run_data_manager)
data_manager_thread.start()

if run_dynamically:
    app = FlaskAppWrapper(Flask(__name__, template_folder=uploader_config["template_folder"]))
    app.run(debug=False, port=uploader_config["PORT"], host=uploader_config["HOST"])
