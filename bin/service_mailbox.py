#!/bin/python
import os
import cleo
import mailbox

cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    mail = mailbox.Mailbox()
    mail.process_messages(cleo)
    os.system("sleep 10")
