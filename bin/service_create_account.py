import getpass
from interfaces.uploader_app import add_username_password

while True:
    username = input("Enter username (or type 'STOP' to quit): ")
    if username.upper() == 'STOP':
        break

    password = getpass.getpass("Enter password: ")
    password_2nd_time = getpass.getpass("Enter password again: ")

    if password == password_2nd_time:
        add_username_password(username, password)
        print("Account created")
        print()
    else:
        print("Passwords did not match, please try again")
        print()

print("Exiting.")