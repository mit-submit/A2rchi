#!/bin/python
from A2rchi.chains.chain import Chain
from A2rchi.interfaces.uploader_app.app import FlaskAppWrapper
from A2rchi.utils.config_loader import Config_Loader
from A2rchi.utils.data_manager import DataManager
from A2rchi.utils.env import read_secret
from A2rchi.utils.scraper import Scraper

from flask import Flask
from piazza_api import Piazza
from threading import Thread

import json
import os
import requests
import time

# DEFINITIONS
SLACK_HEADERS = {'content-type': 'application/json'}
MIN_NEXT_POST_FILE = "/root/data/min_next_post.json"

# set openai
os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
slack_url = read_secret("SLACK_WEBHOOK")
piazza_email = read_secret("PIAZZA_EMAIL")
piazza_password = read_secret("PIAZZA_PASSWORD")
piazza_config = Config_Loader().config["utils"].get("piazza", None)

# scrape data onto the filesystem
scraper = Scraper()
scraper.hard_scrape(verbose=True)
# unresolved_posts = scraper.piazza_scrape(verbose=True)

# update vector store
data_manager = DataManager()
data_manager.update_vectorstore()

# go through unresolved posts and suggest answers

# from this point on; filter feed for new posts and propose answers

# ^also filter for new posts that have been resolved and add to vector store

# for now, just iter through all posts and send replies for unresolved


# login to piazza
piazza = Piazza()
piazza.user_login(email=piazza_email, password=piazza_password)
piazza_net = piazza.network(piazza_config["network_id"])

# create chain
a2rchi_chain = Chain()

def call_chain(chain, post):
    # convert post --> history
    post_str = "SUBJECT: " + post['history'][-1]['subject'] + "\n\nCONTENT: " + post['history'][-1]['content']
    history = [("User", post_str)]

    return chain(history)['answer'], post_str


def write_min_next_post(post_nr):
    with open(MIN_NEXT_POST_FILE, 'w') as f:
        json.dump({"min_next_post_nr": post_nr}, f)


def read_min_next_post():
    with open(MIN_NEXT_POST_FILE, 'r') as f:
        min_next_post_data = json.load(f)
    
    return int(min_next_post_data['min_next_post_nr'])

# # get generator for all posts
# max_post_nr = 0
# posts = piazza_net.iter_all_posts(sleep=1.5)
# for idx, post in enumerate(posts):
#     # update highest post # seen
#     max_post_nr = max(post['nr'], max_post_nr)

#     # if post has no answer or an unresolved followup, send to A2rchi
#     if post.get("no_answer", False):  # or post.get("no_answer_followup", False)
#         print(f"{idx} PROCESSING POST: {post['nr']}")

#         # generate response
#         response, post_str = call_chain(a2rchi_chain, post)
#         response = f"====================\nReplying to Post @{post['nr']}\n==========\n\n{post_str}\n==========\n\nA2RCHI RESPONSE: {response}\n====================\n"

#         # send response to Slack
#         r = requests.post(slack_url, data=json.dumps({"text": response}), headers=SLACK_HEADERS)
#         print(r)

#     else:
#         print(f"{idx} skipping post: {post['nr']}")

# continuously poll for next post
# min_next_post_nr = max_post_nr + 1

# write min next post number if we're initializing for the first time
if not os.path.isfile(MIN_NEXT_POST_FILE):
    print("WRITING INITIAL MIN. NEXT POST")
    write_min_next_post(44)

# read min next post number
min_next_post_nr = read_min_next_post()

while True:
    try:
        # get new post(s) and sort them by 'nr'
        feed = piazza_net.get_feed(limit=999999, offset=0)
        post_nrs = sorted(list(map(lambda post: post['nr'], feed['feed'])))
        largest_post_nr = post_nrs[-1]
    except Exception as e:
        print("ERROR - Failed to parse feed due to the following exception:")
        print(str(e))
        time.sleep(60)
        continue

    # keep processing posts >= min_next_post_nr
    while len(post_nrs) > 0:
        # get next post number
        post_nr = post_nrs.pop(-1)

        # stop if we've already processed it
        if post_nr < min_next_post_nr:
            break

        try:
            # otherwise, process it
            post = piazza_net.get_post(post_nr)

            # if successful, send to A2rchi
            print(f"PROCESSING NEW POST: {post_nr}")
            response, post_str = call_chain(a2rchi_chain, post)
            response = f"====================\nReplying to Post @{post['nr']}\n==========\n\n{post_str}\n==========\n\nA2RCHI RESPONSE: {response}\n====================\n"

            # send response to Slack
            r = requests.post(slack_url, data=json.dumps({"text": response}), headers=SLACK_HEADERS)
            print(r)
        except Exception as e:
            print(f"ERROR - Failed to process post {post_nr} due to the following exception:")
            print(str(e))

    # set min. next post to be one greater than max we just saw
    min_next_post_nr = largest_post_nr + 1

    # write min_next_post_nr so we don't start over on restart
    write_min_next_post(min_next_post_nr)

    # sleep for 60s
    time.sleep(60)