from a2rchi.interfaces.cleo import Cleo

from time import time
import threading

def test_cleo_overall():
    cleo = Cleo('Cleo_Helpdesk')
    cleo.load()
    cleo.process_new_issues()
    cleo.process_feedback_issues()
    cleo.ai_wrapper.chain.kill = True
