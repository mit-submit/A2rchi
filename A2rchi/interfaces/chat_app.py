import os
from typing import Optional, Tuple

import gradio as gr
import numpy as np
import json
import pickle
from threading import Lock

from A2rchi.chains.chain import Chain

from A2rchi.utils.config_loader import Config_Loader
global_config = Config_Loader().config["global"]

QUERY_LIMIT = 1000 #max number of queries 

# TODO: not urgent, but there is a much better way to do this rather than a large dictionary inserted here
source_dict = {
    "backup.txt": "https://submit.mit.edu/submit-users-guide/backup.html",
    "gpu.txt": "https://submit.mit.edu/submit-users-guide/gpu.html",
    "index.txt": "https://submit.mit.edu/submit-users-guide/index.html",
    "intro.txt": "https://submit.mit.edu/submit-users-guide/intro.html",
    "monit.txt": "https://submit.mit.edu/submit-users-guide/monit.html",
    "program.txt": "https://submit.mit.edu/submit-users-guide/program.html",
    "running.txt": "https://submit.mit.edu/submit-users-guide/running.html",
    "starting.txt": "https://submit.mit.edu/submit-users-guide/starting.html",
    "storage.txt": "https://submit.mit.edu/submit-users-guide/storage.html",
    "working.txt": "https://submit.mit.edu/submit-users-guide/working.html",

    "about.html": "https://submit.mit.edu/?page_id=6",
}

class ChatWrapper:
    """Wrapper which holds functionality for the chatbot"""

    def __init__(self):
        self.lock = Lock()
        self.chain = Chain()
        self.number_of_queries = 0

    @staticmethod
    def convert_to_app_history(history):
        """
        Input: the history in the form of a list of tuples, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself (native A2rchi history format)

        Output: the history in the form of a list of tuples, where the first entry of each tuple is
        a question and the second is the response texts (chat format).
        """

        app_history = []
        i = 0
        while i < len(history):
            if i+1 < len(history) and  history[i][0] != "A2rchi" and history[i+1][0] == "A2rchi":
                app_history.append((history[i][1], history[i+1][1]))
                i += 2
            elif history[i][0] != "A2rchi":
                app_history.append((history[i][1], None))
                i += 1
            elif history[i][0] == "A2rchi":
                app_history.append((None, history[i][1]))
                i += 1
        return app_history

    @staticmethod
    def convert_to_chain_history(history):
        """
        Input: the history in the form of a list of tuples, where the first entry of each tuple is
        a question and the second is the response texts (chat format).

        Output: the history in the form of a list of tuples, where the first entry of each tuple is 
        the author of the text and the second entry is the text itself (native A2rchi history format)
        """

        if history is None:
            return history

        chain_history = []
        i = 0
        for entry in history:
            chain_history.append(("User", entry[0]))
            chain_history.append(("A2rchi", entry[1]))
        return chain_history

    @staticmethod
    # TODO: not urgent, but there is a much better way to do this
    def update_or_add_discussion(json_file, discussion_id, discussion_contents):
        # Read the existing JSON data from the file
        try:
            with open(global_config["DATA_PATH"] + json_file, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}  # If the file doesn't exist or is empty, initialize an empty dictionary

        discussion_id = str(discussion_id)
        # Check if the discussion ID exists in the JSON data
        if discussion_id in data.keys():
            # Update the discussion contents for an existing discussion ID
            data[discussion_id] = discussion_contents
        else:
            # Add a new discussion if the discussion ID doesn't exist
            data[discussion_id] = discussion_contents

        # Write the updated JSON data back to the file
        with open(global_config["DATA_PATH"] + json_file, 'w') as f:
            json.dump(data, f)

    def __call__(self, inp: str, history: Optional[Tuple[str, str]], discussion_id: Optional[int]):
        """Execute the chat functionality."""
        self.lock.acquire()
        try:
            #convert to a form that the chain can understand and add the most recent message
            history = ChatWrapper.convert_to_chain_history(history) or []
            history.append(("User", inp))

            #get discussion ID so that the conversation can be saved
            discussion_id = discussion_id or np.random.randint(100000, 999999)

            # Run chain to get result
            if self.number_of_queries < QUERY_LIMIT:
                result = self.chain(history)
            else: 
            #the case where we have exceeded the QUERY LIMIT (built so that we don't overuse the chain)
                history.append(("A2rchi", "Sorry, our service is currently down due to exceptional demand. Please come again later."))
                history = ChatWrapper.convert_to_app_history(history)
                return history, history, discussion_id
            self.number_of_queries += 1
            print("number of queires is: ", self.number_of_queries)

            # Get similarity score to see how close the input is to the source
            # Low score means very close (it's a distance between embedding vectors approximated
            # by an approximate k-nearest neighbors algoirthm)
            self.chain.update_vectorstore()
            score = self.chain.vectorstore.similarity_search_with_score(inp)[0][1]

            #Get the closest source to the document
            source = result['source_documents'][0].metadata['source'].split('/')[-1]

            #If the score is low enough, include the source as a link, otherwise give just the answer
            if score < .4 and source in source_dict.keys(): 
                output = result["answer"] + "\n\n [<b>Click here to read more</b>](" +  source_dict[source] + ")"
            else:
                output = result["answer"]

            history.append(("A2rchi", output))

            ChatWrapper.update_or_add_discussion("conversations_test.json", discussion_id, history)

            history = ChatWrapper.convert_to_app_history(history)

        except Exception as e:
            raise e
        finally:
            self.lock.release()
        return history, history, discussion_id

class Chat_UI:

    def __init__(self):
        self.chat = ChatWrapper()
        self.block = gr.Blocks(css=".gradio-container {background-color: white}")

    @staticmethod
    def clear_last(history, chatbot, id_state):
        """Clears the most recent response so that it may be regenerated"""
        chatbot[-1][1] = None
        id_state = None
        return history[:-1], chatbot, id_state


    def launch(self, _debug = True, _share = True):

        with self.block:

            introduction = "Hello! My name is A2rchi, your friendly guide to subMIT, the computing cluster. Whether you're a beginner or an expert, I'm here to help you navigate through the world of computing. Just ask away, and I'll do my best to assist you!"

            chatbot = gr.Chatbot([[None, introduction]]).style(height=370)
            state = gr.State()
            id_state = gr.State(None)

            with gr.Row():
                with gr.Column(scale=0.85):
                    message = gr.Textbox(
                        label="What's your question?",
                        show_label = False,
                        placeholder="Type your question here and press enter to ask A2rchi",
                        lines=1,
                    ).style(container=False)
                with gr.Column(scale=0.15):
                    regenerate = gr.Button("Regenerate Response")

            clear = gr.Button("Clear")

            gr.Examples(
                examples=[
                    "What is submit?",
                    "How do I install an ssh key?",
                    "How do I change to my work directory?"
                ],
                inputs=message,
            )

            clear.click(lambda: [None, None, "", None], inputs=None, outputs=[chatbot, state, message, id_state])

            regenerate.click(fn = Chat_UI.clear_last, inputs=[state, chatbot, id_state], outputs=[state, chatbot, id_state]).then(fn = self.chat, inputs = [message, state, id_state], outputs=[chatbot, state, id_state])

            message.submit(fn = self.chat, inputs = [message, state, id_state], outputs=[chatbot, state, id_state])

        self.block.launch(debug=_debug, share=_share)
