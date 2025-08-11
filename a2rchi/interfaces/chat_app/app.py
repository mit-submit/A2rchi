from a2rchi.chains.chain import Chain
from a2rchi.utils.config_loader import load_config, CONFIG_PATH
from a2rchi.utils.data_manager import DataManager
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import get_logger
from a2rchi.utils.sql import SQL_INSERT_CONVO, SQL_INSERT_FEEDBACK, SQL_INSERT_TIMING, SQL_QUERY_CONVO, SQL_INSERT_CONFIG

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
from urllib.parse import urlparse

import mistune as mt
import numpy as np

import os
import psycopg2
import psycopg2.extras
import yaml
import json
import time
import re

logger = get_logger(__name__)

# DEFINITIONS
QUERY_LIMIT = 10000 # max queries per conversation
MAIN_PROMPT_FILE = "/root/A2rchi/main.prompt"
CONDENSE_PROMPT_FILE = "/root/A2rchi/condense.prompt"
SUMMARY_PROMPT_FILE = "/root/A2rchi/summary.prompt"


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
        self.config = load_config()
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
        self.config = load_config()
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

        # initialize config_id to be None
        self.config_id = None

    def update_config(self, config_id):
        self.config_id = config_id
        self.chain.update_config()

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
             logger.info("Rendering error: markdown formatting failed")
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

    def prepare_context_for_storage(self, source_documents, sources):

        num_retrieved_docs = len(source_documents)
        context = ""
        if num_retrieved_docs > 0:
            for k in range(num_retrieved_docs):
                document = source_documents[k]
                document_source_hash = document.metadata['source']
                if '/' in document_source_hash and '.' in document_source_hash:
                    document_source_hash = document_source_hash.split('/')[-1].split('.')[0]
                link_k = "link not available"
                if document_source_hash in sources:
                    link_k = sources[document_source_hash]
                multiple_newlines = r'\n{2,}'
                content = re.sub(multiple_newlines, '\n', document.page_content)
                context += f"Source {k+1}: {document.metadata.get('title', 'No Title')} ({link_k})\n\n{content}\n\n\n\n"

        return context

    def insert_conversation(self, conversation_id, user_message, a2rchi_message, link, a2rchi_context, is_refresh=False) -> List[int]:
        """
        """
        logger.debug("Entered insert_conversation.")

        service = "Chatbot"
        # parse user message / a2rchi message
        user_sender, user_content, user_msg_ts = user_message
        a2rchi_sender, a2rchi_content, a2rchi_msg_ts = a2rchi_message

        # construct insert_tups
        insert_tups = (
            [
                # (service, conversation_id, sender, content, context, ts)
                (service, conversation_id, user_sender, user_content, '', '', user_msg_ts, self.config_id),
                (service, conversation_id, a2rchi_sender, a2rchi_content, link, a2rchi_context, a2rchi_msg_ts, self.config_id),
            ]
            if not is_refresh
            else [
                (service, conversation_id, a2rchi_sender, a2rchi_content, link, a2rchi_context, a2rchi_msg_ts, self.config_id),
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
        logger.debug("Entered insert_timing.")

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
            logger.info("Acquired lock file update vectorstore")

            self.data_manager.update_vectorstore()
            timestamps['vectorstore_update_ts'] = datetime.now()

        except Exception as e:
            # NOTE: we log the error message but do not return here, as a failure
            # to update the data manager does not necessarily mean A2rchi cannot
            # process and respond to the message
            logger.error(f"Failed to update vectorstore - {str(e)}")

        finally:
            self.lock.release()
            logger.info("Released lock file update vectorstore")

        try:
            # convert the message to native A2rchi form (because javascript does not have tuples)
            sender, content = tuple(message[0])

            # TODO: incr. from 0?
            # get discussion ID so that the conversation can be saved (It seems that random is no good... TODO)
            conversation_id = conversation_id or np.random.randint(100000, 999999)

            # fetch history given conversation_id
            history = self.query_conversation_history(conversation_id)
            timestamps['query_convo_history_ts'] = datetime.now()

            # if this is a chat refresh / message regeneration; remove previous contiuous non-A2rchi message(s)
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
                result = self.chain(full_history, conversation_id)
                timestamps['chain_finished_ts'] = datetime.now()
            else:
                # for now let's return a timeout error, as returning a different
                # error message would require handling new message_ids param. properly
                return None, None, None, timestamps, 500

            # keep track of total number of queries and log this amount
            self.number_of_queries += 1
            logger.info(f"Number of queries is: {self.number_of_queries}")

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
            logger.debug(f"Similarity score reference:  {similarity_score_reference}")
            logger.debug(f"Similarity score:  {score}")
            link = ""
            if source is not None and score < similarity_score_reference and source in sources.keys():
                link = sources[source]
                logger.info(f"Primary source:  {link}")
                parsed_source = urlparse(link)
                output = "<p>" + self.format_code_in_text(result["answer"]) + "</p>" + "\n\n<br /><br /><p><a href=" + link + " target=\"_blank\" rel=\"noopener noreferrer\">" + parsed_source.hostname + "</a></p>"
            else:
                output = "<p>" + self.format_code_in_text(result["answer"]) + "</p>"

            # write user message and A2rchi response to database
            timestamps['a2rchi_message_ts'] = datetime.now()
            user_message = (sender, content, server_received_msg_ts)
            a2rchi_message = ("A2rchi", output, timestamps['a2rchi_message_ts'])
            context = self.prepare_context_for_storage(result['source_documents'], sources)

            message_ids = self.insert_conversation(conversation_id, user_message, a2rchi_message, link, context, is_refresh)
            timestamps['insert_convo_ts'] = datetime.now()

        except Exception as e:
            # NOTE: we log the error message and return here
            logger.error(f"Failed to produce response: {e}", exc_info=True)
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
        logger.info("Entering FlaskAppWrapper")
        self.app = app
        self.configs(**configs)
        self.config = load_config()
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.data_path = self.global_config["DATA_PATH"]

        # store postgres connection info
        self.pg_config = {
            "password": read_secret("POSTGRES_PASSWORD"),
            **self.utils_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        # insert config
        self.config_id = self.insert_config(self.config)

        # create the chat from the wrapper
        self.chat = ChatWrapper()
        self.chat.update_config(self.config_id)

        # enable CORS:
        CORS(self.app)

        # add endpoints for flask app
        self.add_endpoint('/api/get_chat_response', 'get_chat_response', self.get_chat_response, methods=["POST"])
        self.add_endpoint('/', '', self.index)
        self.add_endpoint('/terms', 'terms', self.terms)
        self.add_endpoint('/api/like', 'like', self.like,  methods=["POST"])
        self.add_endpoint('/api/dislike', 'dislike', self.dislike,  methods=["POST"])
        self.add_endpoint('/api/update_config', 'update_config', self.update_config, methods=["POST"])

    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value

    def add_endpoint(self, endpoint = None, endpoint_name = None, handler = None, methods = ['GET'], *args, **kwargs):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods = methods, *args, **kwargs)

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def insert_config(self, config):
        # TODO: use config_name (and then hash of config string) to determine
        #       if config already exists; if so, don't push new config

        # parse config and config_name
        config_name = self.config["name"]
        config = yaml.dump(self.config)

        # construct insert_tup
        insert_tup = [
            (config, config_name),
        ]

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        psycopg2.extras.execute_values(self.cursor, SQL_INSERT_CONFIG, insert_tup)
        self.conn.commit()
        config_id = list(map(lambda tup: tup[0], self.cursor.fetchall()))[0]

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        return config_id

    def update_config(self):
        """
        Updates the config used by A2rchi for responding to messages. The config
        is parsed and inserted into the `configs` table. Finally, the chat wrapper's
        config_id is updated.
        """
        # parse config and write it out to CONFIG_PATH
        config_str = request.json.get('config')
        with open(CONFIG_PATH, 'w') as f:
            f.write(config_str)

        # parse prompts and write them to their respective locations
        main_prompt = request.json.get('main_prompt')
        with open(MAIN_PROMPT_FILE, 'w') as f:
            f.write(main_prompt)

        condense_prompt = request.json.get('condense_prompt')
        with open(CONDENSE_PROMPT_FILE, 'w') as f:
            f.write(condense_prompt)

        summary_prompt = request.json.get('summary_prompt')
        with open(SUMMARY_PROMPT_FILE, 'w') as f:
            f.write(summary_prompt)

        # re-read config using load_config and update dependent variables
        self.config = load_config()
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.data_path = self.global_config["DATA_PATH"]

        # store postgres connection info
        self.pg_config = {
            "password": read_secret("POSTGRES_PASSWORD"),
            **self.utils_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        # insert config
        self.config_id = self.insert_config(self.config)

        # create the chat from the wrapper
        self.chat = ChatWrapper()
        self.chat.update_config(self.config_id)

        return jsonify({'response': f'config updated successfully w/config_id: {self.config_id}'}), 200


    def get_chat_response(self):
        """
        Gets a response when prompted. Asks as an API to the main app, who's
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
        start_time = time.time()
        server_received_msg_ts = datetime.now()

        # get user input and conversation_id from the request
        message = request.json.get('last_message')
        conversation_id = request.json.get('conversation_id')
        is_refresh = request.json.get('is_refresh')
        client_sent_msg_ts = request.json.get('client_sent_msg_ts') / 1000
        client_timeout = request.json.get('client_timeout') / 1000

        # query the chat and return the results.
        logger.debug("Calling the ChatWrapper()")
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
        try:
            response_size = len(response) if isinstance(response, str) else 0
            logger.info(f"Generated Response Length: {response_size} characters")
            json.dumps({'response': response})  # Validate JSON formatting
        except Exception as e:
            logger.error(f"JSON Encoding Error: {e}")
            response = "Error processing response"

        response_data = {
            'response': response,
            'conversation_id': conversation_id,
            'a2rchi_msg_id': message_ids[-1],
            'server_response_msg_ts': timestamps['server_response_msg_ts'].timestamp(),
            'final_response_msg_ts': datetime.now().timestamp(),
        }

        end_time = time.time()
        logger.info(f"API Response Time: {end_time - start_time:.2f} seconds")

        return jsonify(response_data)

    def index(self):
        return render_template('index.html')

    def terms(self):
        return render_template('terms.html')

    def like(self):
        self.chat.lock.acquire()
        logger.info("Acquired lock file")
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
            logger.error(f"Request failed: {str(e)}")
            return jsonify({'error': str(e)}), 500

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
            logger.info("Released lock file")

            if self.chat.cursor is not None:
                self.chat.cursor.close()
            if self.chat.conn is not None:
                self.chat.conn.close()

    def dislike(self):
        self.chat.lock.acquire()
        logger.info("Acquired lock file")
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
            logger.error(f"Request failed: {str(e)}")
            return jsonify({'error': str(e)}), 500

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
            logger.info("Released lock file")

            if self.chat.cursor is not None:
                self.chat.cursor.close()
            if self.chat.conn is not None:
                self.chat.conn.close()
