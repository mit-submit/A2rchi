#!/bin/python
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import setup_logging, get_logger
from a2rchi.interfaces.uploader_app.app import add_username_password

import getpass
import os

setup_logging()
logger = get_logger(__name__)

# load config and create accounts path if it doesn't exist
global_config = load_config()["global"]
os.makedirs(global_config["ACCOUNTS_PATH"], exist_ok=True)

# read salt
salt = read_secret("UPLOADER_SALT")

while True:
    username = input("Enter username (or type 'STOP' to quit): ")
    if username.upper() == 'STOP':
        break

    password = getpass.getpass("Enter password: ")
    password_2nd_time = getpass.getpass("Enter password again: ")

    if password == password_2nd_time:
        add_username_password(username, password, salt, global_config["ACCOUNTS_PATH"])
        logger.info("Account created")

    else:
        logger.error("Passwords did not match, please try again")


logger.info("Exiting.")
