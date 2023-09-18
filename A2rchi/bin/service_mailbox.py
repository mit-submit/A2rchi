#!/bin/python
from A2rchi.interfaces import cleo
from A2rchi.utils import mailbox
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret

import os
import time

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
print("Starting Mailbox Service")

config = Config_Loader().config["utils"]
cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    mail = mailbox.Mailbox()
    mail.process_messages(cleo)
    time.sleep(int(config["mailbox"]["mailbox_update_time"]))
