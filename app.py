import os
from typing import Optional, Tuple

import gradio as gr
import pickle
#from query_data import get_chain
from threading import Lock

from chain import Chain

"""with open("vectorstore.pkl", "rb") as f:
    vectorstore = pickle.load(f)"""


def set_openai_api_key(api_key: str):
    """Set the api key and return chain.
    If no api_key, then None is returned.
    """
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
        #chain = get_chain(vectorstore)
        #os.environ["OPENAI_API_KEY"] = ""
        #return chain

class ChatWrapper:

    def __init__(self):
        self.lock = Lock()
        m = Chain()
        self.chain = m.chain
    def __call__(
        self, inp: str, history: Optional[Tuple[str, str]], chain
    ):
        """Execute the chat functionality."""
        self.lock.acquire()
        try:
            history = history or []
            # If chain is None, that is because no API key was provided.
            if self.chain is None:
                history.append((inp, "Please paste your OpenAI key to use"))
                return history, history
            # Set OpenAI key
            #import openai
            #openai.api_key = api_key
            # Run chain and append input.
            result = self.chain({"question": inp, "chat_history": history})
            output = result["answer"] + "\n\n <b>Source:</b>  " + result['source_documents'][0].metadata['source'].split('/')[-1] + " pg. " + str(result['source_documents'][0].metadata['page'])
            history.append((inp, output))
        except Exception as e:
            raise e
        finally:
            self.lock.release()
        return history, history

chat = ChatWrapper()

block = gr.Blocks(css=".gradio-container {background-color: lightgray}")

with block:
    with gr.Row():
        gr.Markdown("<h3><center>Submit Chatbot (V0.1.0)</center></h3>")

    chatbot = gr.Chatbot()

    with gr.Row():
        message = gr.Textbox(
            label="What's your question?",
            placeholder="Ask questions concerning help with submit",
            lines=1,
        )
        submit = gr.Button(value="Send", variant="secondary").style(full_width=False)

    gr.Examples(
        examples=[
            "What is submit?",
            "How do I install an ssh key?",
            "How do I change to my work directory?"
        ],
        inputs=message,
    )

    state = gr.State()
    agent_state = gr.State()

    submit.click(fn = chat, inputs = [message, state, agent_state], outputs=[chatbot, state])
    message.submit(fn = chat, inputs = [message, state, agent_state], outputs=[chatbot, state])
    #submit.click(chat, inputs=[message, state, agent_state], outputs=[chatbot, state])
    #message.submit(chat, inputs=[message, state, agent_state], outputs=[chatbot, state])

block.launch(debug=True, share=True)