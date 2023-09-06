#!/bin/python
from A2rchi.interfaces import cleo
from A2rchi.utils import mailbox

import os

print("Starting Mailbox Service")
cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    mail = mailbox.Mailbox()
    mail.process_messages(cleo)
    os.system("sleep 10")
