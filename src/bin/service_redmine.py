#!/bin/python
import os
import time

from src.interfaces.redmine_mailer_integration import redmine
from src.utils.env import read_secret
from src.utils.logging import setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.config_access import get_services_config

# set basicConfig for logging
setup_logging()

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

# temporary hack to prevent redmine, mailbox, and chat services from all
# starting DataManager at the same time; eventually replace this with
# more robust solution
time.sleep(30)

print("Starting Redmine Service")
factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
PostgresServiceFactory.set_instance(factory)

redmine_config = get_services_config().get("redmine_mailbox", {})
redmine_instance = redmine.Redmine('Redmine_Helpdesk')

while True:
    redmine_instance.load()
    redmine_instance.process_new_issues()
    redmine_instance.process_resolved_issues()
    time.sleep(int(redmine_config["redmine_update_time"]))
