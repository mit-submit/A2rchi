from a2rchi.utils.config_loader import load_config
from a2rchi.utils.env import read_secret
from a2rchi.utils.scraper import Scraper
from a2rchi.utils.logging import get_logger

from flask import render_template, request, redirect, url_for, flash, session

import hashlib
import os
import urllib
import yaml

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
    try:
        # load existing accounts or initialize as empty dictionary
        with open(sources_path, 'r') as file:
            sources = yaml.safe_load(file) or {}
    except FileNotFoundError:
        sources = {}

    # check if the url already exists and remove if it does
    sources = {k:v for k,v in sources.items() if v != url}

    # write the updated dictionary back to the YAML file
    with open(sources_path, 'w') as file:
        yaml.dump(sources, file)


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


"""
note: app frontend is in interfaces/templates
"""
class FlaskAppWrapper(object):

    def __init__(self, app, **configs):
        # load global config
        self.global_config = load_config()["global"]
        self.config = load_config()["interfaces"]["uploader_app"]
        self.data_path = self.global_config["DATA_PATH"]
        self.salt = read_secret("UPLOADER_SALT")


        # store flask app and set secret key
        self.app = app
        self.app.secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY")

        # set flask configuration
        self.configs(**configs)
        self.app.config['UPLOAD_FOLDER'] = os.path.join(self.data_path, "manual_uploads")
        self.app.config['WEBSITE_FOLDER'] = os.path.join(self.data_path, "manual_websites")
        self.app.config['ACCOUNTS_FOLDER'] = self.global_config["ACCOUNTS_PATH"]

        # create upload and accounts folders if they don't already exist
        os.makedirs(self.app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(self.app.config['WEBSITE_FOLDER'], exist_ok=True)
        os.makedirs(self.app.config['ACCOUNTS_FOLDER'], exist_ok=True)

        # create path specifying URL sources for scraping
        self.sources_path = os.path.join(self.data_path, 'sources.yml')

        # add endpoints for flask app
        self.add_endpoint('/', '', self.index)
        self.add_endpoint('/index', 'index', self.index)
        self.add_endpoint('/login', 'login', self.login, methods=['GET', 'POST'])
        self.add_endpoint('/logout', 'logout', self.logout)
        self.add_endpoint('/upload', 'upload', self.upload, methods=['POST'])
        self.add_endpoint('/delete/<filename>', 'delete', self.delete)
        self.add_endpoint('/upload_url', 'upload_url', self.upload_url, methods=['POST'])
        self.add_endpoint('/delete_url/<path:encoded_url>', 'delete_url', self.delete_url)


    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value


    def add_endpoint(self, endpoint=None, endpoint_name=None, handler=None, methods=['GET'], *args, **kwargs):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods=methods, *args, **kwargs)


    def run(self, **kwargs):
        self.app.run(**kwargs)


    def is_authenticated(self):
        """
        Keeps the state of the authentication. 

        Returns true if there has been a correct login authentication and false otherwise.
        """
        return 'logged_in' in session and session['logged_in']


    #@app.route('/login', methods=['GET', 'POST'])
    def login(self):
        """
        Method which governs the logging into the system. Relies on check_credentials function
        """
        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']

            if check_credentials(username, password, self.salt, self.app.config['ACCOUNTS_FOLDER']):
                session['logged_in'] = True
                return redirect(url_for('index'))
            else:
                flash('Invalid credentials')

        return render_template('login.html')


    #@app.route('/logout')
    def logout(self):
        """
        Method which is responsible for logout

        This method is never explictly called, login sessions 
        are stored in the cookies.
        """
        session.pop('logged_in', None)

        return redirect(url_for('login'))


    #@app.route('/')
    def index(self):
        """
        Methods which gets all the filenames in the UPLOAD_FOLDER and lists them
        in the UI.

        Note, this method must convert the file hashes (which is the name the files)
        are stored under in the filesystem) to file names. It uses get_filename_from_hash
        for this.
        """
        if not self.is_authenticated():
            return redirect(url_for('login'))

        # get filenames and hashes for files in uploads folder
        file_hashes = os.listdir(self.app.config['UPLOAD_FOLDER'])
        file_names = [get_filename_from_hash(file_hash, self.data_path) for file_hash in file_hashes]

        if os.path.exists(self.sources_path):

            with open(self.sources_path, 'r') as file:
                sources = yaml.safe_load(file) or {}

            url_hashes = os.listdir(self.app.config['WEBSITE_FOLDER'])
            url_names = [sources[url_hash.split(".")[0]] for url_hash in url_hashes]
        else:
            url_names = []

        return render_template('index.html', files=file_names, urls=url_names)


    #@app.route('/upload', methods=['POST'])
    def upload(self):
        """
        Methods which governs uploading.

        Does not allow uploading if the file is not of a valid file type or if the file
        already exists in the filesystem.
        """
        # check that there is a file selected and that the name is not null
        if 'file' not in request.files:
            flash('No file part')
            return redirect(url_for('index'))

        file = request.files['file']
        if file.filename == '':
            flash('No selected file')
            return redirect(url_for('index'))

        # check it is a valid file
        file_extension = os.path.splitext(file.filename)[1]
        if file and file_extension in self.global_config["ACCEPTED_FILES"]:

            # get the file hash and upload file to filesystem see if it is not already present
            filename = file.filename
            if add_filename_to_filehashes(filename, self.data_path):
                files_hash = file_hash(filename)
                file.save(os.path.join(self.app.config['UPLOAD_FOLDER'], files_hash))
                flash('File uploaded successfully')
            else:
                flash('File under this name already exists. If you would like to upload a new file, please delete the old one.')

        else:
            flash('Invalid file, accepted file types are ' + str(self.global_config["ACCEPTED_FILES"]))

        return redirect(url_for('index'))


    #@app.route('/delete/<filename>')
    def delete(self, filename):
        """
        Method which governs deleting

        Technically can handle edge case where the file which is trying to be deleted
        is not in the filesystem.
        """
        file_path = os.path.join(self.app.config['UPLOAD_FOLDER'], file_hash(filename))

        logger.info(f"Deleting the following file: {file_path}")
        if os.path.exists(file_path):
            os.remove(file_path)
            remove_filename_from_filehashes(filename, self.data_path)
            flash('File deleted successfully')

        else:
            flash('File not found')

        return redirect(url_for('index'))


    #@app.route('/upload_url', methods=['POST'])
    def upload_url(self):
        if not self.is_authenticated():
            return redirect(url_for('login'))

        url = request.form.get('url')
        if url:
            logger.info(f"Uploading the following URL: {url}")
            try:
                # same as the scraper, though it doesn't need to be
                Scraper.scrape_urls(
                    urls=[url],
                    upload_dir=self.app.config['WEBSITE_FOLDER'],
                    sources_path=self.sources_path,
                    verify_urls=self.config["verify_urls"],
                    enable_warnings=True,
                )
                added_to_urls = True

            except Exception as e:
                logger.error(f"Failed to upload URL: {str(e)}")
                added_to_urls = False

            if added_to_urls:
                flash('URL uploaded successfully')
            else:
                flash('Failed to add URL')
        else:
            flash('No URL provided')

        return redirect(url_for('index'))


    #@app.route('/delete_url/<path:encoded_url>')
    def delete_url(self, encoded_url):
        url = urllib.parse.unquote(encoded_url)
        file_path = os.path.join(self.app.config['WEBSITE_FOLDER'], simple_hash(url)[0:12] + ".html")
        logger.info(f"Removing the following URL: {file_path}")

        if os.path.exists(file_path):
            os.remove(file_path)
            remove_url_from_sources(url, self.sources_path)
            flash('URL deleted successfully')
        else:
            flash('URL not found')

        return redirect(url_for('index'))
