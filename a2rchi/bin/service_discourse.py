from fluent_discourse import Discourse

import json
import os
import requests
import time
from markdownify import markdownify as md


from a2rchi.utils.env import read_secret
from a2rchi.chain import Chain
from a2rchi.utils.data_manager import DataManager

'''
README

TLDR: still to be tested and debugged, but the code is more or less ready.

The code points to two different systems: the ROOT forum Discourse interface (https://test-root-forum.webtest.cern.ch/), 
for now a test platform on which ludo is admin and has api key; (contact Pietro for secrets) and the CERN mattermost channel (https://mattermost.web.cern.ch/a2rchi/channels/town-square) 
on which we activated webhooks (MATTERMOST_WEBHOOK) and a private access key (PAK), which is stored as a secret on github "MM_PAK". 
The discourse key is stored as secret on github "DISCOURSE_KEY". To send a request the sending machine needs to be in the CERN network, 
hence it should be run from lxplus (CERN's computing framework). Core of this script is the function process_posts, see below.

WHAT STILL NEEDS TO BE DONE: 
 - Connecting the actual chain, making sure the format of the conversation history, as taken in input by the chain, is correct. 
 - Adding the piece to retrieve separately from the chain relevant forum posts (I would suggest feeding A2rchi just with docs and tutorials for now) 
in order to provide root hosts with similar issues that already have an answer. Find a practical way to implement a question cap. 
 - Maybe connect to database for monitoring 
'''


class DiscourseAIWrapper:
    def __init__(self):
        self.chain = Chain()
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()

    def __call__(self, post):

        # form the formatted history using the post
        formatted_history = []
        post_str = "SUBJECT: " + post['history'][-1]['subject'] + "\n\nCONTENT: " + post['history'][-1]['content']
        formatted_history.append(("User", post_str))

        # update vector store
        self.data_manager.update_vectorstore()

        # call chain
        answer = self.chain(formatted_history)["answer"]

        return answer, post_str



class DiscourseMattermost:
    
    "A class to describe a2rchi's interaction with Discouse + Mattermost to process questions on the root forum (Discourse) and provide draft responses (Mattermost) "

    def __init__(self):
        
        # initialize class used to call chain later on
        self.ai_wrapper = DiscourseAIWrapper()

        # (environment) variables to access Discourse and Mattermost
        self.mattermost_headers = {'content-type': 'application/json'}
        self.active_posts_file = '~/data/active_posts.json'
        self.mattermost_url = 'https://mattermost.web.cern.ch/'
        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.mattermost_channel_id = '1p3g6kg19fyzxezats3wyze6oy'
        self.PAK = read_secret("MATTERMOST_PAK")
        self.discourse_url = 'https://test-root-forum.webtest.cern.ch/'
        self.discourse_user = 'lumori'
        self.discourse_key = read_secret("DISCOURSE_KEY")
        self.client = Discourse(base_url=self.discourse_url, username=self.discourse_user, api_key=self.discourse_key, raise_for_rate_limit=True)


    def post_response(self, response,id):
        # send response to MM
        r = requests.post(self.mattermost_webhook, data=json.dumps({"text": response,"channel" : "town-square","root_id": id}), headers=self.mattermost_headers)
        print(r.text)
        return

    def remove_response(self, id):
        # send response to MM
        content = "api/v4/posts/" + id
        r = requests.delete(self.mattermost_url + content, headers= {"Authorization" : f"Bearer {self.PAK}"})
        print(r)
        return

    def clear_channel(self):
        #remove all posts from MM channel
        content = f"api/v4/channels/{self.mattermost_channel_id}/posts"
        r = requests.get(self.mattermost_url + content, headers= {"Authorization" : f"Bearer {self.PAK}"})
        for id in r.json()["order"]:
            self.remove_response(id)
        return

    def get_active_posts(self):
        content = f"api/v4/channels/{self.mattermost_channel_id}/posts"
        r = requests.get(self.mattermost_url + content, headers= {"Authorization" : f"Bearer {self.PAK}"})
        active_posts={}
        for id in r.json()["order"]:
            active_posts[id]=r.json()["posts"][id]["message"]
        return active_posts


    def process_posts(self, active_posts):
        try:
            # get all the latest topics (i.e. threads made of posts by different users or hosts) from the Discourse forum 
            feed = self.client.latest.json.get()["topic_list"]["topics"]
            topic_ids = list(map(lambda topic: topic['id'], feed))
        except Exception as e:
            print("ERROR - Failed to parse feed due to the following exception:")
            print(str(e))
            # time.sleep(60)
            return active_posts

        for topic in feed:
            if topic["reply_count"]!=0 ^ topic["id"] in active_posts.keys(): 
                # cases in which you don't need any processing to take place: if the topic already has a reply on Discourse that is not out on the MM channel 
                # (hence a2rchi does not need to give an answer), or if there is no answer yet on Discourse but the topic has already a suggested answer on MM 
                # (ie a2rchi looked at the topic already and made a proposal)
                continue
            elif topic["reply_count"]!=0 and topic["id"] in active_posts.keys():
                # reply has been published on Discourse: remove suggestion from MM
                active_posts.pop(topic["id"])
                self.remove_response(topic["id"])
                print("removed post ", topic["id"])
                continue
            else:
                try:
                    # otherwise, process it
                    post = self.client.t[topic["id"]].json.get()["post_stream"]["posts"][0]["cooked"]

                    print(f"PROCESSING NEW POST: {topic['id']}")

                    answers+=1 #maybe this needs a cleanupx
                    note = ""
                    
                    answer, post_str = self.ai_wrapper(post)
                    
                    response = f"====================\nReplying to Post @{topic['id']}\n==========\n\nurl: {self.discourse_url}/t/{topic['id']}\n==========\n\n==========\n\n{md(post_str)}\n==========\n\nA2RCHI RESPONSE: {answer}\n====================\n"
                    
                    active_posts[topic['id']] = response
                    self.post_response(response,topic["id"])
                    
                except Exception as e:
                    print(f"ERROR - Failed to process post {topic['id']} due to the following exception:")
                    print(str(e))
                    
        return active_posts

def write_active_posts_file(active_posts):
    with open(ACTIVE_POSTS_FILE, 'w') as f:
        json.dump(active_posts, f)


def read_active_posts_file():
    with open(ACTIVE_POSTS_FILE, 'r') as f:
        return json.load(f)
    
clear_channel()
active_posts = get_active_posts()

while answers<cap:
    # break
    active_posts = process_posts(active_posts)
    print(active_posts.keys())

    # sleep for 60s
    time.sleep(60)

post_response("Maximum A2rchi messages exceeded! Please check for new forum posts on https://test-root-forum.webtest.cern.ch/")
