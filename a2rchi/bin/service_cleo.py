#!/bin/python
from a2rchi.interfaces import cleo
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.env import read_secret

import os
import time

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

# temporary hack to prevent cleo, mailbox, and chat services from all
# starting DataManager at the same time; eventually replace this with
# more robust solution
time.sleep(30)

print("Starting Cleo Service")
config = Config_Loader().config["utils"]
cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    cleo.load()
    cleo.process_new_issues()
    cleo.process_resolved_issues()
    time.sleep(int(config["cleo"]["cleo_update_time"]))
