from a2rchi.chains.chain import Chain
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.data_manager import DataManager
from a2rchi.interfaces.chat_app.user import User
from a2rchi.utils.env import read_secret
from a2rchi.utils.sql import SQL_INSERT_CONVO, SQL_INSERT_FEEDBACK, SQL_INSERT_TIMING, SQL_QUERY_CONVO

from datetime import datetime
from pygments import highlight
from pygments.lexers import (
    BashLexer,
    PythonLexer,
    JavaLexer,
    JavascriptLexer,
    CppLexer,
    CLexer,
    TypeScriptLexer,
    HtmlLexer,
    FortranLexer,
    JuliaLexer,
    MathematicaLexer,
    MatlabLexer
)
from pygments.formatters import HtmlFormatter

from flask import request, jsonify, render_template, request, url_for, flash, redirect
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

import mistune as mt
import numpy as np

import json
import re
import os
import psycopg2
import psycopg2.extras
import yaml
import time
import urllib
import secrets
import requests

# DEFINITIONS
QUERY_LIMIT = 10000 # max queries per conversation

UUID_BYTES = 8

# Configuration
MIT_CLIENT_ID = read_secret("MIT_CLIENT_ID")
MIT_CLIENT_SECRET = read_secret("MIT_CLIENT_SECRET")
MIT_DISCOVERY_URL = "https://oidc.mit.edu/.well-known/openid-configuration"

GOOGLE_CLIENT_ID = read_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = read_secret("GOOGLE_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


class AnswerRenderer(mt.HTMLRenderer):
    """
    Class for custom rendering of A2rchi output. Child of mistune's HTMLRenderer, with custom overrides.
    Code blocks are structured and colored according to pygment lexers
    """
    RENDERING_LEXER_MAPPING = {
            "python": PythonLexer,
            "java": JavaLexer,
            "javascript": JavascriptLexer,
            "bash": BashLexer,
            "c++": CppLexer,
            "cpp": CppLexer,
            "c": CLexer,
            "typescript": TypeScriptLexer,
            "html": HtmlLexer,
            "fortran" : FortranLexer,
            "julia" : JuliaLexer,
            "mathematica" : MathematicaLexer,
            "matlab": MatlabLexer
        }
    
    def __init__(self):
        self.config = Config_Loader().config
        super().__init__()

    def block_text(self,text):
         #Handle blocks of text (the negatives of blocks of code) and sets them in paragraphs
         return f"""<p>{text}</p>"""

    def block_code(self, code, info=None):
        # Handle code blocks (triple backticks)
        if info not in self.RENDERING_LEXER_MAPPING.keys(): info = 'bash' #defaults in bash
        code_block_highlighted = highlight(code.strip(), self.RENDERING_LEXER_MAPPING[info](stripall=True), HtmlFormatter())

        if self.config["interfaces"]["chat_app"]["include_copy_button"]:
            button = """<button class="copy-code-btn" onclick="copyCode(this)"> Copy Code </button>"""
        else: button = ""
        
        return f"""<div class="code-box">
                <div class="code-box-header"> 
                <span>{info}</span>{button}
                </div>
                <div class="code-box-body">{code_block_highlighted}
                </div>
                </div>"""
        
    def codespan(self, text):
        # Handle inline code snippets (single backticks)
        return f"""<code class="code-snippet">{text}</code>"""


class ChatWrapper:
    """
    Wrapper which holds functionality for the chatbot
    """
    def __init__(self):
        # load configs
        self.config = Config_Loader().config
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.data_path = self.global_config["DATA_PATH"]

        # initialize data manager
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()

        # store postgres connection info
        self.pg_config = {
            "password": read_secret("POSTGRES_PASSWORD"),
            **self.utils_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        # initialize lock and chain
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
    def format_code_in_text(text):
        """
        Takes in input plain text (the output from A2rchi); 
        Recognizes structures in canonical Markdown format, and processes according to the custom renderer; 
        Returns it formatted in HTML 
        """
        markdown = mt.create_markdown(renderer=AnswerRenderer())
        try:
            return markdown(text)
        except: 
             print("Rendering error: markdown formatting failed")
             return text


    def insert_feedback(self, feedback):
        """
        Insert feedback from user for specific message into feedback table.
        """
        # construct insert_tup (mid, feedback_ts, feedback, feedback_msg, incorrect, unhelpful, inappropriate)
        insert_tup = (
            feedback['message_id'],
            feedback['feedback_ts'],
            feedback['feedback'],
            feedback['feedback_msg'],
            feedback['incorrect'],
            feedback['unhelpful'],
            feedback['inappropriate'],
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        self.cursor.execute(SQL_INSERT_FEEDBACK, insert_tup)
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None


    def query_conversation_history(self, conversation_id):
        """
        Return the conversation history as an ordered list of tuples. The order
        is determined by ascending message_id. Each tuple contains the sender and
        the message content
        """
        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()

        # query conversation history
        self.cursor.execute(SQL_QUERY_CONVO, (conversation_id,))
        history = self.cursor.fetchall()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        return history


    def insert_conversation(self, conversation_id, user_message, a2rchi_message, is_refresh=False) -> List[int]:
        """
        """
        print(" INFO - entered insert_conversation.")

        # parse user message / a2rchi message
        user_sender, user_content, user_msg_ts = user_message
        a2rchi_sender, a2rchi_content, a2rchi_msg_ts = a2rchi_message

        # construct insert_tups
        insert_tups = (
            [
                # (conversation_id, sender, content, ts)
                (conversation_id, user_sender, user_content, user_msg_ts),
                (conversation_id, a2rchi_sender, a2rchi_content, a2rchi_msg_ts),
            ]
            if not is_refresh
            else [
                (conversation_id, a2rchi_sender, a2rchi_content, a2rchi_msg_ts),
            ]
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        psycopg2.extras.execute_values(self.cursor, SQL_INSERT_CONVO, insert_tups)
        self.conn.commit()
        message_ids = list(map(lambda tup: tup[0], self.cursor.fetchall()))

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        return message_ids
    
    def insert_timing(self, message_id, timestamps):
        """
        Store timing info to understand response profile.
        """
        print(" INFO - entered insert_timing.")

        # construct insert_tup
        insert_tup = (
            message_id, 
            timestamps['client_sent_msg_ts'],
            timestamps['server_received_msg_ts'],
            timestamps['lock_acquisition_ts'],
            timestamps['vectorstore_update_ts'],
            timestamps['query_convo_history_ts'],
            timestamps['chain_finished_ts'],
            timestamps['similarity_search_ts'],
            timestamps['a2rchi_message_ts'],
            timestamps['insert_convo_ts'],
            timestamps['finish_call_ts'],
            timestamps['server_response_msg_ts'],
            timestamps['server_response_msg_ts'] - timestamps['server_received_msg_ts']
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        self.cursor.execute(SQL_INSERT_TIMING, insert_tup)
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None


    def __call__(self, message: List[str], conversation_id: int, is_refresh: bool, server_received_msg_ts: datetime,  client_sent_msg_ts: float, client_timeout: float):
        """
        Execute the chat functionality.
        """
        # store timestamps for code profiling information
        timestamps = {}

        self.lock.acquire()
        timestamps['lock_acquisition_ts'] = datetime.now()
        try:
            # update vector store through data manager; will only do something if new files have been added
            print("INFO - acquired lock file update vectorstore")

            self.data_manager.update_vectorstore()
            timestamps['vectorstore_update_ts'] = datetime.now()

        except Exception as e:
            # NOTE: we log the error message but do not return here, as a failure
            # to update the data manager does not necessarily mean A2rchi cannot
            # process and respond to the message
            print(f"ERROR - {str(e)}")

        finally:
            self.lock.release()
            print("INFO - released lock file update vectorstore")

        try:
            # convert the message to native A2rchi form (because javascript does not have tuples)
            sender, content = tuple(message[0])            

            # TODO: incr. from 0?
            # get discussion ID so that the conversation can be saved (It seems that random is no good... TODO)
            conversation_id = conversation_id or np.random.randint(100000, 999999)

            # fetch history given conversation_id
            history = self.query_conversation_history(conversation_id)
            timestamps['query_convo_history_ts'] = datetime.now()

            # if this is a chat refresh / message regeneration; remove previous contiguous non-A2rchi message(s)
            if is_refresh:
                while history[-1][0] == "A2rchi":
                    _ = history.pop(-1)

            # guard call to LLM; if timestamp from message is more than timeout secs in the past;
            # return error=True and do not generate response as the client will have timed out
            if server_received_msg_ts.timestamp() - client_sent_msg_ts > client_timeout:
                return None, None, None, timestamps, 408

            # run chain to get result; limit users to 1000 queries per conversation; refreshing browser starts new conversation
            if len(history) < QUERY_LIMIT:
                full_history = history + [(sender, content)] if not is_refresh else history
                result = self.chain(full_history)
                timestamps['chain_finished_ts'] = datetime.now()
            else:
                # for now let's return a timeout error, as returning a different
                # error message would require handling new message_ids param. properly
                return None, None, None, timestamps, 500

            # keep track of total number of queries and log this amount
            self.number_of_queries += 1
            print(f"number of queries is: {self.number_of_queries}")

            # get similarity score to see how close the input is to the source
            # - low score means very close (it's a distance between embedding vectors approximated
            #   by an approximate k-nearest neighbors algorithm called HNSW)
            score = self.chain.similarity_search(content)
            timestamps['similarity_search_ts'] = datetime.now()

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
                output = "<p>" + self.format_code_in_text(result["answer"]) + "</p>" + "\n\n<br /><br /><p><a href= " + sources[source] + ">Click here to read more</a></p>"
            else:
                output = "<p>" + self.format_code_in_text(result["answer"]) + "</p>"

            # write user message and A2rchi response to database
            timestamps['a2rchi_message_ts'] = datetime.now()
            user_message = (sender, content, server_received_msg_ts)
            a2rchi_message = ("A2rchi", output, timestamps['a2rchi_message_ts'])

            message_ids = self.insert_conversation(conversation_id, user_message, a2rchi_message, is_refresh)
            timestamps['insert_convo_ts'] = datetime.now()

        except Exception as e:
            # NOTE: we log the error message and return here
            print(f"ERROR - {str(e)}")
            return None, None, None, timestamps, 500

        finally:
            if self.cursor is not None:
                self.cursor.close()
            if self.conn is not None:
                self.conn.close()
        
        timestamps['finish_call_ts'] = datetime.now()

        return output, conversation_id, message_ids, timestamps, None


class FlaskAppWrapper(object):

    def __init__(self, app, **configs):
        print(" INFO - entering FlaskAppWrapper")
        self.app = app
        self.app.secret_key = read_secret("FLASK_APP_SECRET_KEY")  # TODO: REMOVE UPLOADER FROM NAME
        self.configs(**configs)
        self.global_config = Config_Loader().config["global"]
        self.app_config = Config_Loader().config["interfaces"]["chat_app"]
        self.data_path = self.global_config["DATA_PATH"]

        # create the chat from the wrapper
        self.chat = ChatWrapper()

        # configure login
        self.USE_LOGIN = self.app_config["USE_LOGIN"]
        self.ALLOW_GUEST_LOGIN = self.app_config["ALLOW_GUEST_LOGIN"]
        self.GOOGLE_LOGIN = self.app_config["GOOGLE_LOGIN"]
        self.MIT_LOGIN = self.app_config["MIT_LOGIN"]

        # get set of valid users and admins
        self.VALID_USER_EMAILS = self.app_config["ADMIN_USER_EMAILS"] if self.USE_LOGIN else []
        self.VALID_ADMIN_EMAILS = self.app_config["ADMIN_USER_EMAILS"] if self.USE_LOGIN else []
            
        # OAuth 2.0 client setup
        if self.USE_LOGIN:
            self.google_client = WebApplicationClient(GOOGLE_CLIENT_ID) if self.GOOGLE_LOGIN else None
            self.mit_client = WebApplicationClient(MIT_CLIENT_ID) if self.MIT_LOGIN else None


        # enable CORS:
        CORS(self.app)

        # add endpoints for flask app
        self.add_endpoint('/api/get_chat_response', 'get_chat_response', self.get_chat_response, methods=["POST"])
        self.add_endpoint('/', 'index', self.index)
        #self.add_endpoint('/index', 'index', self.index)
        self.add_endpoint('/personal_documents', 'personal_documents', self.personal_documents)
        self.add_endpoint('/master_documents', 'master_documents', self.master_documents)
        self.add_endpoint('/admin_settings', 'admin_settings', self.admin_settings)
        self.add_endpoint('/submit_user_emails', 'submit_user_emails', self.submit_user_emails, methods=["POST"])
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

            conversation_id: Either None or an integer
            last_message:    list of length 2, where the first element is "User"
                             and the second element contains their message.

        Returns:
            A json with a response (html formatted plain text string) and a
            discussion ID (either None or an integer)
        """
        # compute timestamp at which message was received by server
        server_received_msg_ts = datetime.now()

        # get user input and conversation_id from the request
        message = request.json.get('last_message')
        conversation_id = request.json.get('conversation_id')
        is_refresh = request.json.get('is_refresh')
        client_sent_msg_ts = request.json.get('client_sent_msg_ts') / 1000
        client_timeout = request.json.get('client_timeout') / 1000

        # query the chat and return the results.
        print(" INFO - Calling the ChatWrapper()")
        response, conversation_id, message_ids, timestamps, error_code = self.chat(message, conversation_id, is_refresh, server_received_msg_ts, client_sent_msg_ts, client_timeout)

        # handle errors
        if error_code is not None:
            output = (
                jsonify({'error': 'client timeout'})
                if error_code == 408
                else jsonify({'error': 'server error; see chat logs for message'})
            )
            return output, error_code

        # compute timestamp at which message was returned to client
        timestamps['server_response_msg_ts'] = datetime.now()

        # store timing info for this message
        timestamps['server_received_msg_ts'] = server_received_msg_ts
        timestamps['client_sent_msg_ts'] = datetime.fromtimestamp(client_sent_msg_ts)
        self.chat.insert_timing(message_ids[-1], timestamps)

        # otherwise return A2rchi's response to client
        return jsonify({
            'response': response,
            'conversation_id': conversation_id,
            'a2rchi_msg_id': message_ids[-1],
            'server_response_msg_ts': timestamps['server_response_msg_ts'].timestamp(),
            'final_response_msg_ts': datetime.now().timestamp(),
        })

    def render_locked_page(self, page_template_name, admin_only = False, **kwargs):

        admin_page_template_name = page_template_name
        if admin_only:
            page_template_name = "no_access.html"

        # anyone can access chat service
        if not self.USE_LOGIN:
            return render_template(page_template_name, user_name = current_user.name, is_logged_in = "false", is_admin = "false", **kwargs)

        # user is logged in and a guest
        elif self.USE_LOGIN and current_user.is_authenticated and current_user.is_guest:
            return render_template(page_template_name, user_name = current_user.name, is_logged_in = "false", is_admin = "false", **kwargs)
        
        # user is logged in and is admin
        elif self.USE_LOGIN and current_user.is_authenticated and not current_user.is_guest and current_user.email in self.VALID_ADMIN_EMAILS:
            return render_template(admin_page_template_name, user_name = current_user.name, is_logged_in = "true", is_admin = "true",**kwargs)
        
        # user is logged in and is not guest nor admin
        elif self.USE_LOGIN and current_user.is_authenticated and not current_user.is_guest and current_user.email not in self.VALID_ADMIN_EMAILS:
            return render_template(page_template_name, user_name = current_user.name, is_logged_in = "true", is_admin = "false", **kwargs)

        # otherwise, return appropriate login buttons
        else:
            login_buttons = ""
            if self.MIT_LOGIN:
                login_buttons += '<a type="submit" class="login-btn" href="/api/login?provider=mit">Login with Touchstone</a>'

            if self.GOOGLE_LOGIN:
                login_buttons += '<a type="submit" class="login-btn" href="/api/login?provider=google">Login with Google</a>'

            register_link = "<p> Don't have access?<a href='mailto:a2rchi-help@mit.edu'>Request Access</a></p>"
            if self.ALLOW_GUEST_LOGIN:
                register_link = "<p> Don't have access?</p> <p><a href='mailto:a2rchi-help@mit.edu'>Request Access</a> or <a href='/api/guest_login'>Login as guest</a></p>"
            return render_template('login.html', login_buttons=login_buttons, register_link=register_link)


    def index(self):

        return self.render_locked_page("index.html")
        
    def personal_documents(self):
        
        return self.render_locked_page("personal_documents.html")
    
    def master_documents(self):
        
        return self.render_locked_page("master_documents.html", admin_only = True)
    
    def admin_settings(self):

        return self.render_locked_page("admin_settings.html", admin_only=True, original_emails=', '.join(self.VALID_USER_EMAILS))
    
    def submit_user_emails(self):

        #get emails from text box form on admin settings
        emails = request.form['emails']
        email_list = [email.strip() for email in emails.split(',')]

        # check if emails from text box are properly formatted
        for email in email_list:
            email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$' #standard email regular expression
            if re.match(email_regex, email) is None: #check if it is a valid email address
                error_message = "Unable to submit changes: Invalid email found: " + str(email) +". Please make sure all emails are valid and seperated by commas"
                return self.render_locked_page('admin_settings.html', error=error_message, original_emails=emails)
        
        # check if the user emails contain the admin emails
        # if they don't, add back in the admin emails and submit
        if not set(self.VALID_ADMIN_EMAILS).issubset(set(email_list)):
            error_message = "Admin emails must be in the user emails. Automatically adding the following admin emails back in: " + str(set(self.VALID_ADMIN_EMAILS) - set(email_list))
            email_list = list(set(self.VALID_ADMIN_EMAILS).union(set(email_list)))
            self.VALID_USER_EMAILS = email_list
            return self.render_locked_page('admin_settings.html', error=error_message, original_emails=', '.join(self.VALID_USER_EMAILS))
        
        # if emails are properly formatted, update the valid user emails
        self.VALID_USER_EMAILS = email_list
        return self.render_locked_page('admin_settings.html', original_emails=emails)
    
    def terms(self):
        return render_template('terms.html')
    
    def guest_login(self):
        # create a guest user with a unique session
        unique_id = f"{secrets.token_hex(UUID_BYTES)}"
        email = f"guest-{unique_id}"
        user = User(id_=unique_id, email=email, name="Guest")

        # since we generate a unique user each time for guests there should not be a collision
        User.create(unique_id, email, name="Guest")

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
            user_name = userinfo_response.json()["name"]
        else:
            return "User email not available or not verified by provider.", 400

        # if owner of this application has not green-light email; reject user
        if user_email not in valid_user_emails:
            return "User email not authorized for this application.", 401

            # TODO: we could send them to a different landing page w/a link back to index
            #       so they can retry with a diff. email
            # return redirect(url_for('index'))

        # create a user with the information provided by provider
        user = User(id_=unique_id, email=user_email, name  = user_name)

        # add user to db if they don't already exist
        if not User.get(unique_id):
            User.create(unique_id, user_email, user_name)

        # begin user session by logging the user in
        login_user(user)

        # send user back to homepage
        return redirect(url_for("index"))
    
    @login_required
    def logout(self):
        logout_user()
        return redirect(url_for("index"))

    def like(self):
        self.chat.lock.acquire()
        print("INFO - acquired lock file")
        try:
            # Get the JSON data from the request body
            data = request.json

            # Extract the HTML content and any other data you need
            message_id = data.get('message_id')

            feedback = {
                "message_id"   : message_id,
                "feedback"     : "like",
                "feedback_ts"  : datetime.now(),
                "feedback_msg" : None,
                "incorrect"    : None,
                "unhelpful"    : None,
                "inappropriate": None,
            }
            self.chat.insert_feedback(feedback)

            response = {'message': 'Liked'}
            return jsonify(response), 200

        except Exception as e:
            print(f"ERROR: {str(e)}")
            return jsonify({'error': str(e)}), 500

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
            print("INFO - released lock file")

            if self.chat.cursor is not None:
                self.chat.cursor.close()
            if self.chat.conn is not None:
                self.chat.conn.close()

    def dislike(self):
        self.chat.lock.acquire()
        print("INFO - acquired lock file")
        try:
            # Get the JSON data from the request body
            data = request.json

            # Extract the HTML content and any other data you need
            message_id = data.get('message_id')
            feedback_msg = data.get('feedback_msg')
            incorrect = data.get('incorrect')
            unhelpful = data.get('unhelpful')
            inappropriate = data.get('inappropriate')

            feedback = {
                "message_id"   : message_id,
                "feedback"     : "dislike",
                "feedback_ts"  : datetime.now(),
                "feedback_msg" : feedback_msg,
                "incorrect"    : incorrect,
                "unhelpful"    : unhelpful,
                "inappropriate": inappropriate,
            }
            self.chat.insert_feedback(feedback)

            response = {'message': 'Disliked'}
            return jsonify(response), 200

        except Exception as e:
            print(f"ERROR: {str(e)}")
            return jsonify({'error': str(e)}), 500

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
            print("INFO - released lock file")

            if self.chat.cursor is not None:
                self.chat.cursor.close()
            if self.chat.conn is not None:
                self.chat.conn.close()
