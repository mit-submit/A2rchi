#!/bin/python
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret
from A2rchi.interfaces.uploader_app.app import add_username_password

import getpass
import os


# load config and create accounts path if it doesn't exist
global_config = Config_Loader().config["global"]
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
        print("Account created")
        print()
    else:
        print("Passwords did not match, please try again")
        print()

print("Exiting.")
