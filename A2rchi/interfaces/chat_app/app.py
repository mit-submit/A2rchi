from A2rchi.chains.chain import Chain
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.data_manager import DataManager
from A2rchi.utils.env import read_secret
from A2rchi.utils.sql import SQL_INSERT_CONVO, SQL_INSERT_FEEDBACK

from datetime import datetime
from flask import request, jsonify, render_template
from flask_cors import CORS
from threading import Lock
from typing import Optional, List, Tuple

import numpy as np

import os
import psycopg2
import psycopg2.extras
import yaml

# DEFINITIONS
QUERY_LIMIT = 1000 # max number of queries


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
    def convert_to_chain_history(history):
        """
        Input: the history in the form of a list of lists, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself

        Output: the history in the form of a list of tuples, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself (native A2rchi history format)
        """
        return [tuple(entry) for entry in history]


    def insert_feedback(self, conversation_id, feedback):
        """
        """
        # construct insert_tup (cid, mid, feedback_ts, feedback, message, incorrect, unhelpful, inappropriate)
        insert_tup = (
            conversation_id,
            feedback['message_id'],
            feedback['feedback_ts'],
            feedback['feedback'],
            feedback['message'],
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


    # TODO: modify discussion_id --> conversation_id everywhere
    def insert_conversation(self, conversation_id, user_message, a2rchi_message) -> List[int]:
        print(" INFO - entered insert_conversation.")

        # parse user message / a2rchi message if not None
        user_sender, user_content, user_msg_ts = user_message
        a2rchi_sender, a2rchi_content, a2rchi_msg_ts = a2rchi_message

        # construct insert_tups
        insert_tups = [
            # (conversation_id, sender, content, ts)
            (conversation_id, user_sender, user_content, user_msg_ts),
            (conversation_id, a2rchi_sender, a2rchi_content, a2rchi_msg_ts),
        ]

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


    def __call__(self, history: Optional[List[Tuple[str, str]]], discussion_id: Optional[int], msg_ts: Optional[datetime]):
        """
        Execute the chat functionality.
        """
        self.lock.acquire()
        print("INFO - acquired lock file")
        try:
            # update vector store through data manager; will only do something if new files have been added
            self.data_manager.update_vectorstore()

            # convert the history to native A2rchi form (because javascript does not have tuples)
            history = self.convert_to_chain_history(history)

            # TODO: incr. from 0?
            # get discussion ID so that the conversation can be saved (It seems that random is no good... TODO)
            discussion_id = discussion_id or np.random.randint(100000, 999999)

            # TODO: we likely want to change this logic to queries/day as we may actually hit 1000 queries in a few days
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

            # write user message and A2rchi response to database
            user_message = (history[-1][0], history[-1][1], msg_ts)
            a2rchi_message = ("A2rchi", output, datetime.now())

            message_ids = self.insert_conversation(discussion_id, user_message, a2rchi_message)

        except Exception as e:
            raise e
        finally:
            self.lock.release()
            print("INFO - released lock file")

            if self.cursor is not None:
                self.cursor.close()
            if self.conn is not None:
                self.conn.close()

        return output, discussion_id, message_ids


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

            Discussion_id: Either None or an integer
            Conversation: List of length 2 lists, where the length 2
                          lists have first element either "User" or 
                          "A2rchi" and have second element of a message
                          content.

        Returns:
            A json with a response (html formatted plain text string) and a
            discussion ID (either None or an integer)
        """
        # compute timestamp at which message was received by server
        msg_ts = datetime.now()

        # get user input and discussion_id from the request
        history = request.json.get('conversation')
        discussion_id = request.json.get('discussion_id')

        # query the chat and return the results.
        print(" INFO - Calling the ChatWrapper()")
        response, discussion_id, message_ids = self.chat(history, discussion_id, msg_ts)

        return jsonify({'response': response, 'discussion_id': discussion_id, 'a2rchi_msg_id': message_ids[1]})

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
            chat_content = data.get('content')
            discussion_id = data.get('discussion_id')
            message_id = data.get('message_id')

            feedback = {
                "chat_content" : chat_content,
                "message_id"   : message_id,
                "feedback"     : "like",
                "feedback_ts"  : datetime.now(),
                "message"      : None,
                "incorrect"    : None,
                "unhelpful"    : None,
                "inappropriate": None,
            }
            self.chat.insert_feedback(discussion_id, feedback)

            response = {'message': 'Liked', 'content': chat_content}
            return jsonify(response), 200

        except Exception as e:
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
            chat_content = data.get('content')
            discussion_id = data.get('discussion_id')
            message_id = data.get('message_id')
            message = data.get('message')
            incorrect = data.get('incorrect')
            unhelpful = data.get('unhelpful')
            inappropriate = data.get('inappropriate')

            feedback = {
                "chat_content" : chat_content,
                "message_id"   : message_id,
                "feedback"     : "dislike",
                "feedback_ts"  : datetime.now(),
                "message"      : message,
                "incorrect"    : incorrect,
                "unhelpful"    : unhelpful,
                "inappropriate": inappropriate,
            }
            self.chat.insert_feedback(discussion_id, feedback)

            response = {'message': 'Disliked', 'content': chat_content}
            return jsonify(response), 200

        except Exception as e:
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
