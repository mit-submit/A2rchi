#!/bin/python
import os
from threading import Thread

from interfaces.uploader_app import app as uploader_app
from utils.scraper import Scraper
from utils.data_manager import DataManager

from config_loader import Config_Loader
config = Config_Loader().config["utils"]

# decision weather or not to run the vectorstore as a dynamic service or a static one
#
# If http database is enabled, then the service can run dynamically. Thus the vectorstore
# is constantly updated and the uploader app is run
#
# If http database is not enabled, then the service cannot run dynamically. Thus the
# vectorstore is only updated once and the uploader app is not run
run_dynamically = config["data_manager"]["use_HTTP_chromadb_client"]

#scrape data onto the filesystem
scraper=Scraper()
scraper.hard_scrape(verbose=True)

def run_data_manager():
    """
    function which runs the data manager
    """
    stop = False

    data_manager = DataManager()
    while not stop:

        #check to see if this function should only be run once or should be run indefinitely
        if not run_dynamically:
            stop = True 

        #Do updating of vectorstore
        print("Starting update vectorstore")
        data_manager.update_vectorstore()
        print("Completed update vectorstore \n")
        os.system("sleep " + config["data_manager"]["vectordb_update_sleeptime"])

    return

data_manager_thread = Thread(target = run_data_manager)
data_manager_thread.start()

if run_dynamically:
    uploader_app.run(debug=False, port=5003)