from A2rchi.chains.chain import Chain
from A2rchi.utils.config_loader import Config_Loader

from flask import request, jsonify, render_template
from flask_cors import CORS
from threading import Lock
from typing import Optional, List, Tuple

import numpy as np

import json
import os
import yaml
import time

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

        discussion_dict["meta"] = discussion_dict.get("meta", {})
        if str(discussion_id) not in data.keys(): #first time in discusssion
            discussion_dict["meta"]["time_first_used"] = time.time()
        discussion_dict["meta"]["time_last_used"] = time.time()

        if discussion_contents is not None:
            print(" INFO - found contents.")
            discussion_dict["contents"] = discussion_contents
            discussion_dict["meta"]["times_chain_was_called"] = discussion_dict["meta"]["times_chain_was_called"] + [time.time()] if ("times_chain_was_called" in discussion_dict["meta"].keys()) else [time.time()]
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

            ChatWrapper.update_or_add_discussion(self.data_path, "conversations_test.json", discussion_id, discussion_contents = history + [("A2rchi", output)])

        except Exception as e:
            raise e
        finally:
            self.lock.release()
        return output, discussion_id


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
        history = request.json.get('conversation')        # get user input from the request
        discussion_id = request.json.get('discussion_id') # get discussion_id from the request

        # query the chat and return the results. 
        print(" INFO - Calling the ChatWrapper()")
        response, discussion_id = self.chat(history, discussion_id)

        return jsonify({'response': response, 'discussion_id': discussion_id})

    def index(self):
        return render_template('index.html')
    
    def terms(self):
        return render_template('terms.html')
    
    def like(self):
        self.chat.lock.acquire()
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

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()

    def dislike(self):
        self.chat.lock.acquire()
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

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
