#!/bin/python
import os
import time

from src.interfaces import piazza
from src.utils.env import read_secret
from src.utils.logging import setup_logging

# set basicConfig for logging
setup_logging()

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

time.sleep(30) # temporary hack to prevent piazza from starting at the same time as other services; eventually replace this with more robust solution

piazza_agent = piazza.Piazza()
update_time = int(piazza_agent.piazza_config["update_time"])

while True:
    piazza_agent.process_posts()
    time.sleep(update_time)