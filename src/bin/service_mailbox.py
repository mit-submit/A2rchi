#!/bin/python
import os
import time

from src.interfaces.redmine_mailer_integration import mailbox, redmine
from src.utils.env import read_secret
from src.utils.logging import setup_logging, get_logger
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.config_access import get_services_config

# set basicConfig for logging
setup_logging()

logger = get_logger(__name__)
from src.utils.logging import get_logger

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
user = read_secret('IMAP_USER')
password = read_secret('IMAP_PW')

# temporary hack to prevent redmine, mailbox, and chat services from all
# starting DataManager at the same time; eventually replace this with
# more robust solution
time.sleep(60)

print("Starting Mailbox Service")
factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
PostgresServiceFactory.set_instance(factory)

mailbox_config = get_services_config().get("redmine_mailbox", {})
redmine_instance = redmine.Redmine('Redmine_Helpdesk_Mail') # this name tells redmine class to not initialize archi() class

while True:
    try:
        mail = mailbox.Mailbox(user=user, password=password)
        mail.process_messages(redmine_instance)
        time.sleep(int(mailbox_config["mailbox_update_time"]))

    except ConnectionRefusedError as e:
        logger.error(f"Connection refused: {e}")
        logger.info("Retrying in 30 seconds...")
        time.sleep(30)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        logger.info("Retrying in 30 seconds...")
        time.sleep(30)
