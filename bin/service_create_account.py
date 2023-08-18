import getpass
from interfaces.uploader_app import add_username_password

while True:
    username = input("Enter username (or type 'STOP' to quit): ")
    if username.upper() == 'STOP':
        break

    password = getpass.getpass("Enter password: ")
    print("Account created")
    print()

    add_username_password(username, password)

print("Exiting.")