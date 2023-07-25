#!/bin/python
import os
from interfaces import cleo
from utils import mailbox

cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    mail = mailbox.Mailbox()
    mail.process_messages(cleo)
    os.system("sleep 10")
