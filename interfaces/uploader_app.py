import os
import numpy as np
import yaml
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, session
import urllib

from utils.scraper import Scraper

from config_loader import Config_Loader
global_config = Config_Loader().config["global"]

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a secure secret key

# Directory where files will be stored
app.config['UPLOAD_FOLDER'] = global_config["DATA_PATH"] + 'manual_uploads'

##TODO: need to clean up this file


####################################################
####################################################
############### Methods for hashing ################
####################################################
####################################################

def simple_hash(input_string):
    """
    Takes an input string and outputs a hash
    """

    # Perform the hash operation using hashlib
    identifier = hashlib.md5()
    identifier.update(input_string.encode('utf-8'))
    hash_value= str(int(identifier.hexdigest(),16))

    return hash_value[0:12]

def file_hash(filename):
    """
    Takes an input filename and converts it to a hash.
    
    However, unlike `simple_hash` this method keeps the file extension
    at the end of the name
    """

    return simple_hash(filename) + "." + filename.split(".")[-1]

####################################################
####################################################



####################################################
####################################################
####### Methods for maintaining map between ########
#######         filenames and hashes         #######
####################################################
####################################################

def add_filename_to_filehashes(filename, filehashes_yaml_file = "/manual_file_hashes.yaml"):
    """
    Adds a filename and its respective hash to the map between filenames and hashes

    Map is stored as a .yml file in the same path as where the data is stored. Keys are the hashes 
    and values are the filenames

    Returns true if hash was able to be added sucsessfully. Returns false if the hash (and thus likely)
    the filename already exists.
    """
     
    hash_string = file_hash(filename)

    try:
        with open(global_config["DATA_PATH"] + filehashes_yaml_file, 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}  # Load existing hashes or initialize as empty dictionary
    except FileNotFoundError:
        filenames_dict = {}

    # Check if the username already exists
    if hash_string in filenames_dict.keys():
        print(f"File '{filename}' already exists.")
        return False

    # Add the new username and hashed password to the accounts dictionary
    filenames_dict[hash_string] = filename

    # Write the updated dictionary back to the YAML file
    with open(global_config["DATA_PATH"] + filehashes_yaml_file, 'w') as file:
        yaml.dump(filenames_dict, file)

    return True

def remove_filename_from_filehashes(filename, filehashes_yaml_file = "/manual_file_hashes.yaml"):
    """
    Removes a filename and its respective hash from the map between filenames and hashes

    Map is stored as a .yml file in the same path as where the data is stored. Keys are the hashes 
    and values are the filenames

    Always returns true
    """
     
    hash_string = file_hash(filename)

    try:
        with open(global_config["DATA_PATH"]+ filehashes_yaml_file, 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        filenames_dict = {}

    # Check if the username already exists and remove if it does
    if hash_string in filenames_dict.keys():
        filenames_dict.pop(hash_string)

    # Write the updated dictionary back to the YAML file
    with open(global_config["DATA_PATH"] + filehashes_yaml_file, 'w') as file:
        yaml.dump(filenames_dict, file)

    return True

def get_filename_from_hash(hash_string, filehashes_yaml_file = "/manual_file_hashes.yaml"):
    """
    Given a file hash, returns the original file name from the map

    Map is stored as a .yml file in the same path as where the data is stored. Keys are the hashes 
    and values are the filenames
    """

    try:
        with open(global_config["DATA_PATH"] + filehashes_yaml_file, 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        filenames_dict = {}

    if hash_string in filenames_dict.keys():
        return filenames_dict[hash_string]
    
def remove_url_from_sources(url):
    try:
        with open(global_config["DATA_PATH"] +'sources.yml', 'r') as file:
            sources = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        sources = {}

    # Check if the username already exists and remove if it does
    sources = {k:v for k,v in sources.items() if v!=url}

    # Write the updated dictionary back to the YAML file
    with open(global_config["DATA_PATH"]  +'sources.yml', 'w') as file:
        yaml.dump(sources, file)

####################################################
####################################################


    
####################################################
####################################################
######### Methods for account management ###########
####################################################
####################################################

def add_username_password(username, password, file_name='accounts.yaml'):
    """
    Given username and password in string format, this methods hashes the password
    concatenated with a salt (stored as an environment variable) and saves it to 
    the accounts yaml file.

    Keys in the yaml file are usernames and passwords are the hashes of the password
    plus the salt.

    Adding a username and password for an existing username will overwrite the password
    """
    hash = simple_hash(password + os.environ["UPLOADER_SALT"])
    
    try:
        with open(global_config["DATA_PATH"] + file_name, 'r') as file:
            accounts = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        accounts = {}

    # Check if the username already exists
    if username in accounts:
        print(f"Username '{username}' already exists.")
        return

    # Add the new username and hashed password to the accounts dictionary
    accounts[username] = hash

    # Write the updated dictionary back to the YAML file
    with open(global_config["DATA_PATH"] + file_name, 'w') as file:
        yaml.dump(accounts, file)


def check_credentials(username, password, file_name='accounts.yaml'):
    """
    Given username and password in string format, this methods returns true if the 
    username and password match the stored username and password. This is done by 
    hashing the password and checking that it is the same as the hashed password
    which is stored under the specific username.

    Keys in the yaml file are usernames and passwords are the hashes of the password
    plus the salt.
    """

    #create the hash
    hash = simple_hash(password + os.environ["UPLOADER_SALT"])

    #open the accounts dictionary (if file exists, account dictionary is null)
    try:
        with open(global_config["DATA_PATH"] + file_name, 'r') as file:
            accounts = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        accounts = {}

    #check if hash matches the hash in the accounts dictionary
    if username in accounts and accounts[username] == hash:
        return True
    else:
        return False

####################################################
####################################################



####################################################
####################################################
############# Methods for app backend ##############
####################################################
####################################################

"""
note: app frontend is in interfaces/templates
"""

def is_authenticated():
    """
    Keeps the state of the authentication. 

    Returns true if there has been a correct login authentication and false otherwise.
    """
    return 'logged_in' in session and session['logged_in']


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Method which governs the logging into the system. Relys on check_credentials function
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if check_credentials(username, password):
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """
    Method which is responsible for logout

    This method is never explictly called, login sessions 
    are stored in the cookies.
    """
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route('/')
def index():
    """
    Methods which gets all the filenames in data/"UPLOAD_FOLDER" and lists them
    in the UI.

    Note, this method must convert the file hashes (which is the name the files)
    are stored under in the filesystem) to file names. It uses get_filename_from_hash
    for this.
    """
    if not is_authenticated():
        return redirect(url_for('login'))

    if not os.path.isdir(app.config['UPLOAD_FOLDER']):
                os.mkdir(app.config['UPLOAD_FOLDER'])

    file_hashes = os.listdir(app.config['UPLOAD_FOLDER'])
    file_names = [get_filename_from_hash(file_hash) for file_hash in file_hashes]

    if os.path.isdir(global_config["DATA_PATH"]+'manual_websites/') and os.path.exists(global_config["DATA_PATH"]+'sources.yml'):

        with open(global_config["DATA_PATH"]+'sources.yml', 'r') as file:
            sources = yaml.safe_load(file) or {}

        url_hashes = os.listdir(global_config["DATA_PATH"]+'manual_websites/') 
        url_names = [sources[url_hash.split(".")[0]] for url_hash in url_hashes]
    else:
        url_names = []

    return render_template('index.html', files=file_names, urls = url_names)


@app.route('/upload', methods=['POST'])
def upload():
    """
    Methods which governs uploading.

    Does not allow uploading if the file is not of a valid file type or if the file
    already exists in the filesystem.
    """

    #Check that there is a file selected and that the name is not null
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('index'))
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('index'))

    #Check it is a valid file
    file_extension = file.filename[file.filename.rfind("."):].lower()
    if file and file_extension in global_config["ACCEPTED_FILES"]:

        #Get the file hash and see if it is already in the filesystem. If not, add it to the filesystem by its hash.
        filename = file.filename
        added_to_hashes = add_filename_to_filehashes(filename) #True if there was not already a filename in the manual uploads under this name
        if added_to_hashes:
            files_hash = file_hash(filename)
            if not os.path.isdir(app.config['UPLOAD_FOLDER']):
                    os.mkdir(app.config['UPLOAD_FOLDER'])
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], files_hash))
            flash('File uploaded successfully')
        else:
            flash('File under this name already exists. If you would like to upload a new file, please delete the old one.')

    else:
        flash('Invalid file, accepted file types are ' + str(global_config["ACCEPTED_FILES"]))

    return redirect(url_for('index'))


@app.route('/delete/<filename>')
def delete(filename):
    """
    Method which governs deleting

    Technically can handle edge case where the file which is trying to be deleted
    is not in the filesystem.
    """
    if not os.path.isdir(app.config['UPLOAD_FOLDER']):
                os.mkdir(app.config['UPLOAD_FOLDER'])
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_hash(filename))

    print(file_path)
    if os.path.exists(file_path):
        os.remove(file_path)
        remove_filename_from_filehashes(filename)
        flash('File deleted successfully')
    else:
        flash('File not found')

    return redirect(url_for('index'))

@app.route('/upload_url', methods=['POST'])
def upload_url():
    if not is_authenticated():
        return redirect(url_for('login'))

    url = request.form.get('url')

    if url:
        print("url is: ", url)
        try:
            if not os.path.exists(global_config["DATA_PATH"]+'manual_websites'):
                os.mkdir(global_config["DATA_PATH"]+'manual_websites')
            Scraper.scrape_urls(urls = [url],
                                upload_dir = global_config["DATA_PATH"]+'manual_websites', #same as the scraper, though it doesn't need to be
                                sources_path = global_config["DATA_PATH"]+'sources.yml')
            added_to_urls = True
        except Exception as e:
            print(e)
            added_to_urls = False
        if added_to_urls:
            flash('URL uploaded successfully')
        else:
            flash('Failed to add URL')
    else:
        flash('No URL provided')

    return redirect(url_for('index'))

@app.route('/delete_url/<path:encoded_url>')
def delete_url(encoded_url):

    url = urllib.parse.unquote(encoded_url)

    if not os.path.isdir(global_config["DATA_PATH"]+'manual_websites/'):
                os.mkdir(global_config["DATA_PATH"]+'manual_websites/')
    file_path = os.path.join(global_config["DATA_PATH"]+'manual_websites/', simple_hash(url)[0:12] + ".html")

    print(file_path)
    if os.path.exists(file_path):
        os.remove(file_path)
        remove_url_from_sources(url)
        flash('URL deleted successfully')
    else:
        flash('URL not found')

    return redirect(url_for('index'))

####################################################
####################################################