from A2rchi.interfaces.chat_app import Chat_UI
from A2rchi.utils.env import read_secret

import os

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
print("Starting Chat Service")

chat_ui = Chat_UI()
chat_ui.launch()