import hashlib
import os
import yaml
from pathlib import Path

from src.utils.logging import get_logger
from src.data_manager.collectors.utils.index_utils import load_index, \
    write_index

logger = get_logger(__name__)

def simple_hash(input_string):
    """
    Takes an input string and outputs a hash
    """

    # perform the hash operation using hashlib
    identifier = hashlib.md5()
    identifier.update(input_string.encode('utf-8'))
    hash_value= str(int(identifier.hexdigest(), 16))

    return hash_value


def file_hash(filename):
    """
    Takes an input filename and converts it to a hash.
    
    However, unlike `simple_hash` this method keeps the file extension
    at the end of the name
    """

    return simple_hash(filename)[0:12] + "." + filename.split(".")[-1]


def add_filename_to_filehashes(filename, data_path, filehashes_yaml_file="manual_file_hashes.yaml"):
    """
    Adds a filename and its respective hash to the map between filenames and hashes

    Map is stored as a .yml file in the same path as where the data is stored. Keys are the hashes 
    and values are the filenames

    Returns true if hash was able to be added sucsessfully. Returns false if the hash (and thus likely)
    the filename already exists.
    """
    hash_string = file_hash(filename)
    try:
        # load existing hashes or initialize as empty dictionary
        with open(os.path.join(data_path, filehashes_yaml_file), 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}
    except FileNotFoundError:
        filenames_dict = {}

    # check if the file already exists
    if hash_string in filenames_dict.keys():
        logger.info(f"File '{filename}' already exists.")
        return False

    # add the new filename and hashed file string to the accounts dictionary
    filenames_dict[hash_string] = filename

    # write the updated dictionary back to the YAML file
    with open(os.path.join(data_path, filehashes_yaml_file), 'w') as file:
        yaml.dump(filenames_dict, file)

    return True

#TODO: Obsolete?
def remove_filename_from_filehashes(filename, data_path, filehashes_yaml_file="manual_file_hashes.yaml"):
    """
    Removes a filename and its respective hash from the map between filenames and hashes

    Map is stored as a .yml file in the same path as where the data is stored. Keys are the hashes 
    and values are the filenames

    Always returns true
    """
    hash_string = file_hash(filename)
    try:
        # load existing accounts or initialize as empty dictionary
        with open(os.path.join(data_path, filehashes_yaml_file), 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}
    except FileNotFoundError:
        filenames_dict = {}

    # check if the filename already exists and remove if it does
    if hash_string in filenames_dict.keys():
        filenames_dict.pop(hash_string)

    # write the updated dictionary back to the YAML file
    with open(os.path.join(data_path, filehashes_yaml_file), 'w') as file:
        yaml.dump(filenames_dict, file)

    return True


def get_filename_from_hash(hash_string, data_path, filehashes_yaml_file="manual_file_hashes.yaml"):
    """
    Given a file hash, returns the original file name from the map chat_app
    """
    try:
        # load existing accounts or initialize as empty dictionary
        with open(os.path.join(data_path, filehashes_yaml_file), 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}
    except FileNotFoundError:
        filenames_dict = {}

    return filenames_dict[hash_string] if hash_string in filenames_dict else None


def remove_url_from_sources(url, sources_path):
    data_path = Path(sources_path).parent
    index_data = load_index(data_path)
    sources = index_data.get("sources", {})

    # remove any entry whose value matches the URL
    sources = {k: v for k, v in sources.items() if v != url}
    index_data["sources"] = sources

    write_index(data_path, index_data)


def add_username_password(username, password, salt, accounts_path, file_name='accounts.yaml'):
    """
    Given username and password in string format, this method hashes the password
    concatenated with a salt (stored as an environment variable) and saves it to 
    the accounts yaml file.

    Keys in the yaml file are usernames and passwords are the hashes of the password
    plus the salt.

    Adding a username and password for an existing username will overwrite the password
    """
    hash = simple_hash(password + salt)
    try:
        # load existing accounts or initialize as empty dictionary
        with open(os.path.join(accounts_path, file_name), 'r') as file:
            accounts = yaml.safe_load(file) or {}
    except FileNotFoundError:
        accounts = {}

    # check if the username already exists
    if username in accounts:
        logger.info(f"Username '{username}' already exists.")
        return

    # add the new username and hashed password to the accounts dictionary
    accounts[username] = hash

    # write the updated dictionary back to the YAML file
    with open(os.path.join(accounts_path, file_name), 'w') as file:
        yaml.dump(accounts, file)


def check_credentials(username, password, salt, accounts_path, file_name='accounts.yaml'):
    """
    Given username and password in string format, this methods returns true if the 
    username and password match the stored username and password. This is done by 
    hashing the password and checking that it is the same as the hashed password
    which is stored under the specific username.

    Keys in the yaml file are usernames and passwords are the hashes of the password
    plus the salt.
    """

    # create the hash
    hash = simple_hash(password + salt)

    # open the accounts dictionary (if file exists, account dictionary is null)
    try:
        # load existing accounts or initialize as empty dictionary
        with open(os.path.join(accounts_path, file_name), 'r') as file:
            accounts = yaml.safe_load(file) or {}
            logger.info(f"Accounts are: {accounts}")

    except FileNotFoundError:
        accounts = {}

    # check if hash matches the hash in the accounts dictionary
    return (username in accounts) and (accounts[username] == hash)