#!/bin/python
import os
from A2rchi.interfaces import cleo
from A2rchi.utils.env import read_secret

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
print("Starting Cleo Service")

cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    cleo.load()
    cleo.process_new_issues()
    cleo.process_resolved_issues()
    os.system("sleep 10")
