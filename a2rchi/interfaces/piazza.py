from a2rchi.chains.a2rchi import A2rchi
from a2rchi.interfaces.uploader_app.app import FlaskAppWrapper
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.data_manager import DataManager
from a2rchi.utils.env import read_secret
from a2rchi.utils.scraper import Scraper
from a2rchi.utils.logging import get_logger

from flask import Flask
from piazza_api import Piazza as PiazzaAPI
from threading import Thread

import json
import os
import requests
import time

logger = get_logger(__name__)

class PiazzaAIWrapper:
    def __init__(self):
        # initialize and update vector store
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()

        # intialize chain
        self.a2rchi = A2rchi(pipeline="QAPipeline")

    def __call__(self, post):

        # post --> history for qa chain
        post_str = "SUBJECT: " + post['history'][-1]['subject'] + "\n\nCONTENT: " + post['history'][-1]['content']
        history = [("User", post_str)]

        answer = self.a2rchi(history=history)["answer"]

        return answer, post_str
    



class Piazza:
    """
    Class to go through unresolved posts in Piazza and suggest answers.
    Filter feed for new posts and propose answers.
    Also filter for new posts that have been resolved and add to vector store.
    For now, just iterate through all posts and send replies for unresolved.
    """
    def __init__(self):

        logger.info("Initializing Piazza service")

        self.piazza_config = load_config()["services"].get("piazza", None)

        # login to piazza
        self.piazza = PiazzaAPI()
        self.piazza_email = read_secret("PIAZZA_EMAIL")
        self.piazza_password = read_secret("PIAZZA_PASSWORD")
        self.piazza.user_login(email=self.piazza_email, password=self.piazza_password)
        self.piazza_net = self.piazza.network(self.piazza_config["network_id"])

        # slack webhook for sending draft responses
        self.slack_url = read_secret("SLACK_WEBHOOK")
        self.slack_headers = {'content-type': 'application/json'}

        # 
        self.min_next_post_file = "/root/data/min_next_post.json"
        self.min_next_post_nr = self.read_min_next_post()

        # initialize PiazzaAIWrapper
        self.ai_wrapper = PiazzaAIWrapper()

    def write_min_next_post(self, min_next_post_nr):
        try:
            # create directory if it does not exist
            os.makedirs(os.path.dirname(self.min_next_post_file), exist_ok=True)
            with open(self.min_next_post_file, "w") as f:
                json.dump({"min_next_post_nr": min_next_post_nr}, f)
            logger.info(f"Updated min_next_post_nr to {min_next_post_nr}")
        except Exception as e:
            logger.error(f"Failed to write min_next_post_nr to file: {e}")
            

    def read_min_next_post(self):
        if not os.path.exists(self.min_next_post_file):
            # create directory if it does not exist
            os.makedirs(os.path.dirname(self.min_next_post_file), exist_ok=True)
            # get latest post nr from piazza feed
            try:
                feed = self.piazza_net.get_feed(limit=999999, offset=0)
                if feed['feed']:
                    post_nrs = sorted(list(map(lambda post: post['nr'], feed['feed'])))
                    latest_post_nr = post_nrs[-1] if post_nrs else 0
                    dynamic_min_next_post_nr = latest_post_nr + 1
                    logger.info(f"No min next post file found, using latest post nr {latest_post_nr} + 1 = {dynamic_min_next_post_nr} as default.")
                else:
                    # in case no posts exist
                    dynamic_min_next_post_nr = 1
                    logger.info("No posts found in feed, setting dynamic_min_next_post_nr to 1.")
            except Exception as e:
                logger.error(f"Failed to parse feed: {e}")
                raise Exception(f"Failed to get latest post nr from feed") from e

            self.write_min_next_post(dynamic_min_next_post_nr)
            return dynamic_min_next_post_nr
        

        with open(self.min_next_post_file, "r") as f:
            data = json.load(f)
            return data.get("min_next_post_nr")
    
    # for now just processes "main" posts, i.e. not replies/follow-ups
    def process_posts(self):
        try:
            # get new post(s) and sort them by 'nr'
            feed = self.piazza_net.get_feed(limit=999999, offset=0)
            post_nrs = sorted(list(map(lambda post: post['nr'], feed['feed'])))
            if not post_nrs:
                logger.info("No posts found in feed, skipping this cycle.")
                return
            largest_post_nr = post_nrs[-1]
        except Exception as e:
            logger.error(f"Failed to parse feed due to the following exception: {e}")
            return
            
        new_post_nrs = [post_nr for post_nr in post_nrs if post_nr >= self.min_next_post_nr]
        logger.info(f"Found {len(new_post_nrs)} new posts since last run (min_next_post_nr: {self.min_next_post_nr}, largest_post_nr: {largest_post_nr})")
        if not new_post_nrs:
            logger.info("No new posts to process.")
            return

        # process 
        for post_nr in new_post_nrs:
            try:
                post = self.piazza_net.get_post(post_nr)

                logger.info(f"PROCESSING NEW POST: {post_nr}")
                response, post_str = self.ai_wrapper(post)
                response = f"====================\nReplying to Post @{post['nr']}\n==========\n\n{post_str}\n==========\n\nA2RCHI RESPONSE: {response}\n====================\n"

                # send response to Slack
                r = requests.post(self.slack_url, data=json.dumps({"text": response}), headers=self.slack_headers)
                logger.info(r)
                time.sleep(1)  # to avoid hitting rate limits
            except Exception as e:
                logger.error(f"Failed to process post {post_nr} due to the following exception: {e}")

        if post_nrs:
            # set min. next post to be one greater than max we just saw
            self.min_next_post_nr = largest_post_nr + 1
            # write min_next_post_nr so we don't start over on restart
            self.write_min_next_post(self.min_next_post_nr)
