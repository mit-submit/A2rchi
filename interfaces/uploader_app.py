import os
import numpy as np
import yaml
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, session

from config_loader import Config_Loader
global_config = Config_Loader().config["global"]

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a secure secret key

# Directory where files will be stored
app.config['UPLOAD_FOLDER'] = global_config["DATA_PATH"] + 'manual_uploads'


def simple_hash(input_string):

    # Perform the hash operation using hashlib
    identifier = hashlib.md5()
    identifier.update(input_string.encode('utf-8'))
    hash_value= str(int(identifier.hexdigest(),16))

    return hash_value[0:12]

def file_hash(filename):

    return simple_hash(filename) + "." + filename.split(".")[-1]

def add_filename_to_filehashes(filename, filehashes_yaml_file = "/manual_file_hashes.yaml"):
     
    hash_string = file_hash(filename)

    try:
        with open(global_config["DATA_PATH"] + filehashes_yaml_file, 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
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

    try:
        with open(global_config["DATA_PATH"] + filehashes_yaml_file, 'r') as file:
            filenames_dict = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        filenames_dict = {}

    if hash_string in filenames_dict.keys():
        return filenames_dict[hash_string]

def add_username_password(username, password, file_name='accounts.yaml'):
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
    hash = simple_hash(password + os.environ["UPLOADER_SALT"])

    try:
        with open(global_config["DATA_PATH"] + file_name, 'r') as file:
            accounts = yaml.safe_load(file) or {}  # Load existing accounts or initialize as empty dictionary
    except FileNotFoundError:
        accounts = {}

    if username in accounts and accounts[username] == hash:
        return True
    else:
        return False

def is_authenticated():
    return 'logged_in' in session and session['logged_in']


@app.route('/login', methods=['GET', 'POST'])
def login():
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
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route('/')
def index():
    if not is_authenticated():
        return redirect(url_for('login'))

    if not os.path.isdir(app.config['UPLOAD_FOLDER']):
                os.mkdir(app.config['UPLOAD_FOLDER'])
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    filenames = [get_filename_from_hash(file_hash) for file_hash in files]
    return render_template('index.html', files=filenames)


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(url_for('index'))

    file_extension = file.filename[file.filename.rfind("."):].lower()
    if file and file_extension in global_config["ACCEPTED_FILES"]:
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