from interfaces.chat_app import Chat_UI
from interfaces.cleo import Cleo

from time import time
import threading

#TODO: add better unit tests


def test_cleo_overall():
    cleo = Cleo('Cleo_Helpdesk')
    cleo.load()
    cleo.process_new_issues()
    cleo.process_feedback_issues()
