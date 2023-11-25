import os

def read_secret(secret_name):
    # fetch filepath from env variable
    secret_filepath = os.getenv(f"{secret_name}_FILE")

    # read secret from file and return
    with open(secret_filepath, 'r') as f:
        secret = f.read()

    return secret.strip()
