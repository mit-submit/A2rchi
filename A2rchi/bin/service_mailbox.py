#!/bin/python
from a2rchi.interfaces import cleo
from a2rchi.utils import mailbox
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.env import read_secret

import os
import time

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
user = read_secret('IMAP_USER')
password = read_secret('IMAP_PW')

# temporary hack to prevent cleo, mailbox, and chat services from all
# starting DataManager at the same time; eventually replace this with
# more robust solution
time.sleep(60)

print("Starting Mailbox Service")
config = Config_Loader().config["utils"]
cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    mail = mailbox.Mailbox(user = user, password = password)
    mail.process_messages(cleo)
    time.sleep(int(config["mailbox"]["mailbox_update_time"]))
