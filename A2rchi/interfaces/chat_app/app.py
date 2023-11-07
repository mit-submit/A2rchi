from A2rchi.chains.chain import Chain
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.data_manager import DataManager
from A2rchi.utils.env import read_secret
from A2rchi.utils.sql import SQL_INSERT_CONVO, SQL_INSERT_FEEDBACK, SQL_QUERY_CONVO

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

from flask import request, jsonify, render_template
from flask_cors import CORS
from threading import Lock
from typing import List

import mistune as mt
import numpy as np

import os
import psycopg2
import psycopg2.extras
import yaml

# DEFINITIONS
QUERY_LIMIT = 10000 # max number of queries per conversation


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

        # parse user message / a2rchi message if not None
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


    def __call__(self, message: List[str], conversation_id: int, is_refresh: bool, msg_ts: datetime):
        """
        Execute the chat functionality.
        """
        self.lock.acquire()
        try:
            # update vector store through data manager; will only do something if new files have been added
            print("INFO - acquired lock file update vectorstore")

            self.data_manager.update_vectorstore()

        except Exception as e:
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

            # if this is a chat refresh / message regeneration; remove previous contiguous non-A2rchi message(s)
            if is_refresh:
                while history[-1][0] == "A2rchi":
                    _ = history.pop(-1)

            # run chain to get result; limit users to 1000 queries per conversation; refreshing browser starts new conversation
            if len(history) < QUERY_LIMIT:
                full_history = history + [(sender, content)] if not is_refresh else history
                result = self.chain(full_history)
            else:
                # the case where we have exceeded the QUERY LIMIT (built so that we do not overuse the chain)
                output = "Sorry, our service is currently down due to exceptional demand. Please come again later."
                return output, conversation_id

            # keep track of total number of queries and log this amount
            self.number_of_queries += 1
            print(f"number of queries is: {self.number_of_queries}")

            # get similarity score to see how close the input is to the source
            # - low score means very close (it's a distance between embedding vectors approximated
            #   by an approximate k-nearest neighbors algorithm called HNSW)
            score = self.chain.similarity_search(content)

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
            user_message = (sender, content, msg_ts)
            a2rchi_message = ("A2rchi", output, datetime.now())

            message_ids = self.insert_conversation(conversation_id, user_message, a2rchi_message, is_refresh)

        except Exception as e:
            print(f"ERROR - {str(e)}")

        finally:
            if self.cursor is not None:
                self.cursor.close()
            if self.conn is not None:
                self.conn.close()

        return output, conversation_id, message_ids


class FlaskAppWrapper(object):

    def __init__(self, app, **configs):
        print(" INFO - entering FlaskAppWrapper")
        self.app = app
        self.configs(**configs)
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]

        # create the chat from the wrapper
        self.chat = ChatWrapper()

        # enable CORS:
        CORS(self.app)

        # add endpoints for flask app
        self.add_endpoint('/api/get_chat_response', 'get_chat_response', self.get_chat_response, methods=["POST"])
        self.add_endpoint('/', '', self.index)
        self.add_endpoint('/terms', 'terms', self.terms)
        self.add_endpoint('/api/like', 'like', self.like,  methods=["POST"])
        self.add_endpoint('/api/dislike', 'dislike', self.dislike,  methods=["POST"])

    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value

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
        msg_ts = datetime.now()

        # get user input and conversation_id from the request
        message = request.json.get('last_message')
        conversation_id = request.json.get('conversation_id')
        is_refresh = request.json.get('is_refresh')

        # query the chat and return the results.
        print(" INFO - Calling the ChatWrapper()")
        response, conversation_id, message_ids = self.chat(message, conversation_id, is_refresh, msg_ts)

        return jsonify({'response': response, 'conversation_id': conversation_id, 'a2rchi_msg_id': message_ids[-1]})

    def index(self):
        return render_template('index.html')

    def terms(self):
        return render_template('terms.html')

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
