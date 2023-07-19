import os
from typing import Optional, Tuple

import gradio as gr
import pickle
from threading import Lock

from chain import Chain

# TODO: not urgent, but there is a much better way to do this rather than a large dictionary, 
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
        m = Chain()
        self.chain = m.chain
        self.vectorstore = m.vectorstore
    def __call__(self, inp: str, history: Optional[Tuple[str, str]]):
        """Execute the chat functionality."""
        self.lock.acquire()
        try:
            history = history or []

            # Run chain to get result
            result = self.chain({"question": inp, "chat_history": history})

            # Get similarity score to see how close the input is to the source
            # Low score means very close (it's a distance between embedding vectors approximated
            # by an approximate k-nearest neighbors algoirthm)
            score = self.vectorstore.similarity_search_with_score(inp)[0][1]

            #Get the closest source to the document
            source = result['source_documents'][0].metadata['source'].split('/')[-1]

            #If the score is low enough, include the source as a link, otherwise give just the answer
            if score < .4 and source in source_dict.keys(): 
                output = result["answer"] + "\n\n [<b>Click here to read more</b>](" +  source_dict[source] + ")"
            else:
                output = result["answer"]
            history.append((inp, output))

        except Exception as e:
            raise e
        finally:
            self.lock.release()
        return history, history

def clear_last(history, chatbot):
    """Clears the most recent response so that it may be regenerated"""
    chatbot[-1][1] = None
    return history[:-1], chatbot

chat = ChatWrapper()

block = gr.Blocks(css=".gradio-container {background-color: white}")

with block:

    introduction = "Hello! My name is A2rchi, your friendly guide to subMIT, the computing cluster. Whether you're a beginner or an expert, I'm here to help you navigate through the world of computing. Just ask away, and I'll do my best to assist you! \n \n Some tips and tricks for how to use me best: \n - When starting a new topic of conversation, make sure to hit the clear button at the bottom \n - If I find a useful source for my answer to your question, I will link it below my response. If you need more help, you can either click on the link or ask me follow up questions. \n If you are unsatisfied with my answer, feel free to click the regenerate button \n - I do best with question pertaining to subMIT, if you have other questions not pertaining to subMIT, another chatbot may be more fit for you."

    chatbot = gr.Chatbot([[None, introduction]]).style(height=500)
    state = gr.State()

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

    clear.click(lambda: [None, None, ""], inputs=None, outputs=[chatbot, state, message])

    regenerate.click(fn = clear_last, inputs=[state, chatbot], outputs=[state, chatbot]).then(fn = chat, inputs = [message, state], outputs=[chatbot, state])

    message.submit(fn = chat, inputs = [message, state], outputs=[chatbot, state])

block.launch(debug=True, share=True)
