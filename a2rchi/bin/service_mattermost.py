#!/bin/python
from a2rchi.utils.env import read_secret
from a2rchi.interfaces import mattermost
from a2rchi.utils.logging import setup_logging

import os
import time
import multiprocessing as mp

# set basicConfig for logging
setup_logging()

def main():
    # set openai
    os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
    os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
    os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

    time.sleep(30) # temporary hack to prevent mattermost from starting at the same time as other services; eventually replace this with more robust solution

    print("Initializing Mattermost Service")
    mattermost_agent = mattermost.Mattermost()
    update_time = int(mattermost_agent.mattermost_config["update_time"])

    while True:
        mattermost_agent.process_posts()
        time.sleep(update_time)

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
