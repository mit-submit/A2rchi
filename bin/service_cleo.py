#!/bin/python
import os
from interfaces import cleo

cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    cleo.load()
    cleo.process_new_issues()
    cleo.process_feedback_issues()
    os.system("sleep 10")
