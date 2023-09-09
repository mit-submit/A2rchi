from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import numpy as np
import json
import time
import os
import yaml
from typing import Optional, Tuple, List
from threading import Lock

from chains.chain import Chain

from utils.config_loader import Config_Loader
config = Config_Loader().config
global_config = config["global"]

QUERY_LIMIT = 1000 # max number of queries 

class ChatWrapper:
    """
    Wrapper which holds functionality for the chatbot
    """
    def __init__(self):
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

    @staticmethod                                       # TODO: not urgent, but there is a much better way to do this
    def update_or_add_discussion(json_file, discussion_id, discussion_contents):
        print(" INFO - entered update_or_add_discussion.")
        
        # read the existing JSON data from the file
        try:
            with open(global_config["DATA_PATH"] + json_file, 'r') as f:
                data = json.load(f)
            print(" INFO - json_file found.")
        except FileNotFoundError:
            data = {}  # if the file doesn't exist or is empty, initialize an empty dictionary
            print(" ERROR - json_file not found. Creating a new one")

        discussion_id = str(discussion_id)
        # check if the discussion ID exists in the JSON data
        if discussion_id in data.keys():
            # update the discussion contents for an existing discussion ID
            data[discussion_id] = discussion_contents
        else:
            # add a new discussion if the discussion ID doesn't exist
            data[discussion_id] = discussion_contents

        # check if target folders exist 
        if not os.path.isdir(global_config["DATA_PATH"]):
                os.mkdir(global_config["DATA_PATH"])

        # write the updated JSON data back to the file
        with open(global_config["DATA_PATH"] + json_file, 'w') as f:
            json.dump(data, f)

    def __call__(self, history: Optional[list[str, str]], discussion_id: Optional[int]):
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
            print("number of queries is: ", self.number_of_queries)

            # get similarity score to see how close the input is to the source
            # - low score means very close (it's a distance between embedding vectors approximated
            #   by an approximate k-nearest neighbors algorithm called HNSW)
            inp = history[-1][1]
            similarity_result = self.chain.vectorstore.similarity_search_with_score(inp)
            if len(similarity_result)>0:
                score = self.chain.vectorstore.similarity_search_with_score(inp)[0][1]
            else:
                score = 1e10

            # load the present list of sources
            try:
                with open(global_config["DATA_PATH"]+'sources.yml', 'r') as file:
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
            embedding_name = config["utils"]["embeddings"]["EMBEDDING_NAME"]
            similarity_score_reference = config["utils"]["embeddings"]["EMBEDDING_CLASS_MAP"][embedding_name]["similarity_score_reference"]
            if score < similarity_score_reference and source in sources.keys(): 
                output = "<p>" + result["answer"] + "</p>" + "\n\n<br /><br /><p><a href= " + sources[source] + ">Click here to read more</a></p>"
            else:
                output = "<p>" + result["answer"] + "</p>"

            ChatWrapper.update_or_add_discussion("conversations_test.json", discussion_id, history)

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

        # create the chat from the wrapper
        self.chat = ChatWrapper()

        # enable CORS:
        CORS(self.app)

        # add endpoints for flask app
        self.add_endpoint('/get_chat_response', 'get_chat_response', self.get_chat_response, methods=["POST"])
        self.add_endpoint('/', '', self.index)

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
        history = request.json.get('converstation')       # get user input from the request
        discussion_id = request.json.get('discussion_id') # get discussion_id from the request

        # query the chat and return the results. 
        print(" INFO - Calling the ChatWrapper()")
        response, discussion_id = self.chat(history, discussion_id)
        return jsonify({'response': response, 'discussion_id': discussion_id})
    
    def index(self):
        return render_template('index.html')
    
if __name__ == '__main__':
    app = FlaskAppWrapper(Flask(__name__))
    app.run(debug = True, port = 7680, host = "0.0.0.0")
