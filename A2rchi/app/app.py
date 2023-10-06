#!/bin/python
# Internal imports
from A2rchi.app.user import User
from A2rchi.chains.chain import Chain
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.env import read_secret

# Third-party libraries
from flask import jsonify, redirect, render_template, request, url_for
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_cors import CORS
from functools import partial
from oauthlib.oauth2 import WebApplicationClient
from threading import Lock
from typing import Optional, List, Tuple

import numpy as np

import json
import os
import requests
import secrets
import yaml

# DEFINITIONS
QUERY_LIMIT = 1000 # max number of queries
UUID_BYTES = 8

# Configuration
MIT_CLIENT_ID = read_secret("MIT_CLIENT_ID")
MIT_CLIENT_SECRET = read_secret("MIT_CLIENT_SECRET")
MIT_DISCOVERY_URL = "https://oidc.mit.edu/.well-known/openid-configuration"

GOOGLE_CLIENT_ID = read_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = read_secret("GOOGLE_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


class ChatWrapper:
    """
    Wrapper which holds functionality for the chatbot
    """
    def __init__(self):
        # load configs
        self.config = Config_Loader().config
        self.global_config = self.config["global"]
        self.data_path = self.global_config["DATA_PATH"]

        self.lock = Lock()
        self.chain = Chain()
        self.number_of_queries = 0


    @staticmethod
    def convert_to_app_history(history):
        """
        Input: the history in the form of a list of tuples, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself (native A2rchi history format)

        Output: the history in the form of a list of lists, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself 
        """
        return [list(entry) for entry in history]


    @staticmethod
    def convert_to_chain_history(history):
        """
        Input: the history in the form of a list of lists, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself

        Output: the history in the form of a list of tuples, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself (native A2rchi history format)
        """
        return [tuple(entry) for entry in history]


    @staticmethod
    def update_or_add_discussion(data_path, json_file, discussion_id, discussion_contents = None, discussion_feedback = None):
        print(" INFO - entered update_or_add_discussion.")

        # read the existing JSON data from the file
        data = {}
        try:
            with open(os.path.join(data_path, json_file), 'r') as f:
                data = json.load(f)
            print(" INFO - json_file found.")

        except FileNotFoundError:
            print(" ERROR - json_file not found. Creating a new one")

        # update or add discussion
        discussion_dict = data.get(str(discussion_id), {})

        if discussion_contents is not None:
            print(" INFO - found contents.")
            discussion_dict["contents"] = discussion_contents
        if discussion_feedback is not None:
            print(" INFO - found feedback.")
            discussion_dict["feedback"] = discussion_dict["feedback"] + [discussion_feedback] if ("feedback" in discussion_dict.keys() and isinstance(discussion_dict["feedback"], List)) else [discussion_feedback]
        
        data[str(discussion_id)] = discussion_dict

        # create data path if it doesn't exist
        os.makedirs(data_path, exist_ok=True)

        # write the updated JSON data back to the file
        with open(os.path.join(data_path, json_file), 'w') as f:
            json.dump(data, f)


    def __call__(self, history: Optional[List[Tuple[str, str]]], discussion_id: Optional[int]):
        """
        Execute the chat functionality.
        """
        self.lock.acquire()
        try:
            # convert the history to native A2rchi form (because javascript does not have tuples)
            history = self.convert_to_chain_history(history)

            # get discussion ID so that the conversation can be saved (It seems that random is no good... TODO)
            discussion_id = discussion_id or np.random.randint(100000, 999999)

            # run chain to get result
            if self.number_of_queries < QUERY_LIMIT:
                result = self.chain(history)
            else: 
                # the case where we have exceeded the QUERY LIMIT (built so that we do not overuse the chain)
                output = "Sorry, our service is currently down due to exceptional demand. Please come again later."
                return output, discussion_id
            self.number_of_queries += 1
            print(f"number of queries is: {self.number_of_queries}")

            # get similarity score to see how close the input is to the source
            # - low score means very close (it's a distance between embedding vectors approximated
            #   by an approximate k-nearest neighbors algorithm called HNSW)
            inp = history[-1][1]
            score = self.chain.similarity_search(inp)

            # load the present list of sources
            try:
                with open(os.path.join(self.data_path, 'sources.yml'), 'r') as file:
                    sources = yaml.load(file, Loader=yaml.FullLoader)
            except FileNotFoundError:
                sources = dict()

            # get the closest source to the document
            source = None
            if len(result['source_documents']) > 0:
                source_hash = result['source_documents'][0].metadata['source']
                if '/' in source_hash and '.' in source_hash:
                    source = source_hash.split('/')[-1].split('.')[0]

            # if the score is low enough, include the source as a link, otherwise give just the answer
            embedding_name = self.config["utils"]["embeddings"]["EMBEDDING_NAME"]
            similarity_score_reference = self.config["utils"]["embeddings"]["EMBEDDING_CLASS_MAP"][embedding_name]["similarity_score_reference"]
            if score < similarity_score_reference and source in sources.keys(): 
                output = "<p>" + result["answer"] + "</p>" + "\n\n<br /><br /><p><a href= " + sources[source] + ">Click here to read more</a></p>"
            else:
                output = "<p>" + result["answer"] + "</p>"

            ChatWrapper.update_or_add_discussion(self.data_path, "conversations_test.json", discussion_id, discussion_contents = history)

        except Exception as e:
            raise e
        finally:
            self.lock.release()

        return output, discussion_id










class FlaskAppWrapper(object):

    def __init__(self, app, **configs):
        print(" INFO - entering FlaskAppWrapper")
        self.app = app
        self.app.secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY")  # TODO: REMOVE UPLOADER FROM NAME
        self.configs(**configs)
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]

        # create the chat from the wrapper
        self.chat = ChatWrapper()

        # configure login
        self.USE_LOGIN = self.global_config["USE_LOGIN"]
        self.ALLOW_GUEST_LOGIN = self.global_config["ALLOW_GUEST_LOGIN"]
        self.GOOGLE_LOGIN = self.global_config["GOOGLE_LOGIN"]
        self.MIT_LOGIN = self.global_config["MIT_LOGIN"]
        if self.USE_LOGIN:
            # get set of valid users
            self.VALID_USER_EMAILS = self.global_config["USER_EMAILS"] if self.USE_LOGIN else []

            # OAuth 2.0 client setup
            self.google_client = WebApplicationClient(GOOGLE_CLIENT_ID) if self.GOOGLE_LOGIN else None
            self.mit_client = WebApplicationClient(MIT_CLIENT_ID) if self.MIT_LOGIN else None

        # enable CORS:
        CORS(self.app)

        # add endpoints for flask app
        self.add_endpoint('/api/get_chat_response', 'get_chat_response', self.get_chat_response, methods=["POST"])
        self.add_endpoint('/', 'index', self.index)
        self.add_endpoint('/terms', 'terms', self.terms)
        self.add_endpoint('/api/like', 'like', self.like,  methods=["POST"])
        self.add_endpoint('/api/dislike', 'dislike', self.dislike,  methods=["POST"])

        if self.USE_LOGIN:
            self.add_endpoint('/api/login', 'login', self.login, methods=["GET", "POST"])
            self.add_endpoint('/api/logout', 'logout', self.logout, methods=["GET", "POST"])

        if self.USE_LOGIN and self.ALLOW_GUEST_LOGIN:
            self.add_endpoint('/api/guest_login', 'guest_login', self.guest_login, methods=["GET", "POST"])

        if self.GOOGLE_LOGIN:
            google_callback = partial(
                FlaskAppWrapper.callback,
                get_provider_cfg=self.get_google_provider_cfg,
                client=self.google_client,
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                valid_user_emails=self.VALID_USER_EMAILS,
            )
            self.add_endpoint('/api/login/google-callback', 'api/login/google-callback', google_callback, methods=["GET", "POST"])

        if self.MIT_LOGIN:
            mit_callback = partial(
                FlaskAppWrapper.callback,
                get_provider_cfg=self.get_mit_provider_cfg,
                client=self.mit_client,
                client_id=MIT_CLIENT_ID,
                client_secret=MIT_CLIENT_SECRET,
                valid_user_emails=self.VALID_USER_EMAILS,
            )
            self.add_endpoint('/api/login/mit-callback', 'api/login/mit-callback', mit_callback, methods=["GET", "POST"])

    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value

    def get_google_provider_cfg(self):
        return requests.get(GOOGLE_DISCOVERY_URL).json()

    def get_mit_provider_cfg(self):
        return requests.get(MIT_DISCOVERY_URL).json()

    def add_endpoint(self, endpoint = None, endpoint_name = None, handler = None, methods = ['GET'], *args, **kwargs):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods = methods, *args, **kwargs)

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def get_chat_response(self):
        """
        Gets a response when prompted.Asks as an API to the main app, who's
        functionality is carried through by javascript and html. Input is a 
        requestion with

            Discussion_id: Either None or an integer
            Conversation: List of length 2 lists, where the length 2
                          lists have first element either "User" or 
                          "A2rchi" and have second element of a message
                          content.

        Returns:
            A json with a response (html formatted plain text string) and a
            discussion ID (either None or an integer)
        """
        history = request.json.get('conversation')        # get user input from the request
        discussion_id = request.json.get('discussion_id') # get discussion_id from the request

        # query the chat and return the results. 
        print(" INFO - Calling the ChatWrapper()")
        response, discussion_id = self.chat(history, discussion_id)

        return jsonify({'response': response, 'discussion_id': discussion_id})

    def index(self):
        # anyone can access chat service
        if not self.USE_LOGIN:
            return render_template('index.html')

        # user is logged in (note: guest users still "log in")
        elif self.USE_LOGIN and current_user.is_authenticated:
            return render_template('index.html')

        # otherwise, return appropriate login buttons
        else:
            # return render_template('login.html')
            login_buttons = ""
            if self.MIT_LOGIN:
                login_buttons += '<a class="button" href="/api/login?provider=mit">MIT Login</a><br><br>'

            if self.GOOGLE_LOGIN:
                login_buttons += '<a class="button" href="/api/login?provider=google">Google Login</a><br><br>'

            if self.ALLOW_GUEST_LOGIN:
                login_buttons += '<a class="button" href="/api/guest_login">Guest Login</a><br><br>'

            return login_buttons
        
    def guest_login(self):
        # create a guest user with a unique session
        unique_id = f"{secrets.token_hex(UUID_BYTES)}"
        email = f"guest-{unique_id}"
        user = User(id_=unique_id, email=email)

        # since we generate a unique user each time for guests there should not be a collision
        User.create(unique_id, email)

        # begin user session by logging the user in
        login_user(user)

        # send user back to homepage
        return redirect(url_for("index"))
    
    def login(self):
        # parse query parameter specifying OAuth provider
        provider = request.args.get('provider')

        # set config, client, and callback based on provider
        provider_cfg, client, callback = None, None, None
        if provider == "google":
            provider_cfg = self.get_google_provider_cfg()
            client = self.google_client
            callback = "/google-callback"

        elif provider == "mit":
            provider_cfg = self.get_mit_provider_cfg()
            client = self.mit_client
            callback = "/mit-callback"

        # get authorization endpoint from provider config
        authorization_endpoint = provider_cfg["authorization_endpoint"]

        # get client and construct request URI w/scopes for openid, email, and profile
        request_uri = client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=request.base_url + callback,
            scope=["openid", "email", "profile"],
        )

        return redirect(request_uri)

    @staticmethod
    def callback(get_provider_cfg, client, client_id, client_secret, valid_user_emails):
        # get authorization code provider sent back
        code = request.args.get("code")

        # fetch URL to get tokens that allow us to ask for user's email + info from provider
        provider_cfg = get_provider_cfg()
        token_endpoint = provider_cfg["token_endpoint"]

        # Prepare and send a request to get access token(s)
        token_url, headers, body = client.prepare_token_request(
            token_endpoint,
            authorization_response=request.url,
            redirect_url=request.base_url,
            code=code
        )
        token_response = requests.post(
            token_url,
            headers=headers,
            data=body,
            auth=(client_id, client_secret),
        )

        # parse access token(s)
        client.parse_request_body_response(json.dumps(token_response.json()))

        # fetch user's profile information from provider; we will only keep unique_id and user_email
        provider_cfg = get_provider_cfg()
        userinfo_endpoint = provider_cfg["userinfo_endpoint"]
        uri, headers, body = client.add_token(userinfo_endpoint)
        userinfo_response = requests.get(uri, headers=headers, data=body)

        # parse info if user email is verified
        if userinfo_response.json().get("email_verified"):
            unique_id = userinfo_response.json()["sub"]
            user_email = userinfo_response.json()["email"]
        else:
            return "User email not available or not verified by provider.", 400

        # if owner of this application has not green-light email; reject user
        if user_email not in valid_user_emails:
            return "User email not authorized for this application.", 401

            # TODO: we could send them to a different landing page w/a link back to index
            #       so they can retry with a diff. email
            # return redirect(url_for('index'))

        # create a user with the information provided by provider
        user = User(id_=unique_id, email=user_email)

        # add user to db if they don't already exist
        if not User.get(unique_id):
            User.create(unique_id, user_email)

        # begin user session by logging the user in
        login_user(user)

        # send user back to homepage
        return redirect(url_for("index"))
    
    @login_required
    def logout(self):
        logout_user()
        return redirect(url_for("index"))

    def terms(self):
        return render_template('terms.html')
    
    def like(self):
        try:
            # Get the JSON data from the request body
            data = request.json

            # Extract the HTML content and any other data you need
            chat_content = data.get('content')
            discussion_id = data.get('discussion_id')
            message_id = data.get('message_id')

            feedback = {
                "chat_content" :  chat_content,
                "message_id"   :  message_id,
                "feedback"     :  "like",
            }
            ChatWrapper.update_or_add_discussion(self.data_path, "conversations_test.json", discussion_id, discussion_feedback = feedback)

            response = {'message': 'Liked', 'content': chat_content}
            return jsonify(response), 200


        except Exception as e:
            return jsonify({'error': str(e)}), 500
        
    def dislike(self):
        try:
            # Get the JSON data from the request body
            data = request.json

            # Extract the HTML content and any other data you need
            chat_content = data.get('content')
            discussion_id = data.get('discussion_id')
            message_id = data.get('message_id')
            message = data.get('message')
            incorrect = data.get('incorrect')
            unhelpful = data.get('unhelpful')
            inappropriate = data.get('inappropriate')

            feedback = {
                "chat_content" :  chat_content,
                "message_id"   :  message_id,
                "feedback"     :  "dislike",
                "message"      :  message,
                "incorrect"    :  incorrect,
                "unhelpful"    :  unhelpful,
                "inappropriate":  inappropriate,
            }
            ChatWrapper.update_or_add_discussion(self.data_path, "conversations_test.json", discussion_id, discussion_feedback = feedback)

            response = {'message': 'Disliked', 'content': chat_content}
            return jsonify(response), 200


        except Exception as e:
            return jsonify({'error': str(e)}), 500

