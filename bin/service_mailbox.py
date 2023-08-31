#!/bin/python
import time
from interfaces import cleo
from utils import mailbox
from config_loader import Config_Loader

config = Config_Loader().config["utils"]
cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    mail = mailbox.Mailbox()
    mail.process_messages(cleo)
    time.sleep(int(config["mailbox"]["mailbox_update_time"]))
