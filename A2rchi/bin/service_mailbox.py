#!/bin/python
from A2rchi.interfaces import cleo
from A2rchi.utils import mailbox
from A2rchi.utils.env import read_secret

import os

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
print("Starting Mailbox Service")

cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    import time
    time.sleep(3600)
    mail = mailbox.Mailbox()
    mail.process_messages(cleo)
    os.system("sleep 10")
