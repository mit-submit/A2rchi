#!/bin/python
import time
from interfaces import cleo
from config_loader import Config_Loader

config = Config_Loader().config["utils"]
cleo = cleo.Cleo('Cleo_Helpdesk')

while True:
    cleo.load()
    cleo.process_new_issues()
    cleo.process_resolved_issues()
    time.sleep(int(config["cleo"]["cleo_update_time"]))
