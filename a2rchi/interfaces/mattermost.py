from a2rchi.chains.chain import Chain
from a2rchi.interfaces.uploader_app.app import FlaskAppWrapper
from a2rchi.utils.config_loader import Config_Loader
from a2rchi.utils.data_manager import DataManager
from a2rchi.utils.env import read_secret
from a2rchi.utils.scraper import Scraper

from flask import Flask
from threading import Thread

import json
import os
import requests
import time

class MattermostAIWrapper:
    def __init__(self):
        # initialize and update vector store
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()

        # intialize chain
        self.chain = Chain()

    def __call__(self, post):

        # form the formatted history using the post
        formatted_history = []

        post_str = post['message']
        formatted_history.append(("User", post_str)) 

        # update vector store
        self.data_manager.update_vectorstore()

        # call chain
        answer = self.chain(formatted_history)["answer"]
        print('ANSWER = ',answer)

        return answer, post_str

class Mattermost:
    """
    Class to go through unresolved posts in Mattermost and suggest answers.
    Filter feed for new posts and propose answers.
    Also filter for new posts that have been resolved and add to vector store.
    For now, just iterate through all posts and send replies for unresolved.
    """
    def __init__(self):

        print('Mattermost::INIT')

        self.mattermost_config = Config_Loader().config["utils"].get("mattermost", None)
        
        # mattermost webhook for reading questions/sending responses
        self.mattermost_url = 'https://mattermost.web.cern.ch/'
        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.mattermost_channel_id_read = read_secret("MATTERMOST_CHANNEL_ID_READ")
        self.mattermost_channel_id_write = read_secret("MATTERMOST_CHANNEL_ID_WRITE")
        self.PAK = read_secret("MATTERMOST_PAK")        
        self.mattermost_headers = {
            'Authorization': f'Bearer {self.PAK}',
            'Content-Type': 'application/json'
        }

        print('mattermost_webhook =', self.mattermost_webhook)
        print('mattermost_channel_id_read =', self.mattermost_channel_id_read)        
        print('mattermost_channel_id_write =', self.mattermost_channel_id_write)
        print('PAK =', self.PAK)

        # initialize MattermostAIWrapper
        self.ai_wrapper = MattermostAIWrapper()

        #
        self.min_next_post_file = "/root/data/LPC2025/min_next_post.json"

    def post_response(self, response):

#        url = f"{self.mattermost_url}/api/v4/posts"
#        print('GOING TO WRITE HERE: ',url)

#        payload = {
#            "channel_id": self.mattermost_channel_id_write,
#            "message": response
#        }
        # send response to MM
        #r = requests.post(url, data=json.dumps(payload), headers=self.mattermost_headers)
        r = requests.post(self.mattermost_webhook, data=json.dumps({"text": response,"channel" : "town-square"}), headers=self.mattermost_headers)

        print(r.text)
        print("STATUS CODE",r.status_code)
        return

    def write_min_next_post(self, answered_key):
        try:
            # create directory if it does not exist
            os.makedirs(os.path.dirname(self.min_next_post_file), exist_ok=True)
            with open(self.min_next_post_file, "w") as f:
                json.dump({"answered_id": answered_key}, f)
            print(f"Updated answered_id {answered_key}")
        except Exception as e:
            print(f"ERROR - Failed to write answered_key to file: {e}")

    def get_active_posts(self):

        print('HELLO inside get_active_posts')
        content = f"api/v4/channels/{self.mattermost_channel_id_read}/posts"
        r = requests.get(self.mattermost_url + content, headers=self.mattermost_headers)
        active_posts={}
        for id in r.json()["order"]:
            active_posts[id]=r.json()["posts"][id]["message"]
        print('ACTIVE POST',active_posts)
        return active_posts

    def get_last_post(self):

        content = f"api/v4/channels/{self.mattermost_channel_id_read}/posts"

        r = requests.get(self.mattermost_url + content, headers=self.mattermost_headers)
        data = r.json()
        posts = data.get('posts', {})
        excluded_a2rchi_id = "ajb6wyizpinqir7m16owntod7o"
        filtered_posts = {
            post_id: post
            for post_id, post in posts.items()
            if post.get("user_id") != excluded_a2rchi_id
        }
        sorted_posts = sorted(filtered_posts.values(), key=lambda x: x['create_at'], reverse=True)

        if sorted_posts:
            latest = sorted_posts[0]
            print(f"User ID: {latest['user_id']}")
            print(f"Message: {latest['message']}")
        else:
            print("Mattermost: No messages found.")

        return sorted_posts[0]

    def checkAnswerExist(self, tmpID):

        # Check if file exists
        if not os.path.exists(self.min_next_post_file):
            print("File does not exist, creating new one.")
            data = {"answered_id": []}  # Initialize with empty list
            return False
        else:
            # Load existing data
            with open(self.min_next_post_file, "r") as f:
                data = json.load(f)
                print("Loaded data:", data)

        answered_ids = data.get("answered_id", [])

        # Ensure it's a list
        if not isinstance(answered_ids, list):
            answered_ids = [answered_ids]

        # Only append if not already in the list (optional)
        if tmpID in answered_ids:
            print(f"{tmpID} already exists")
            return True
        else:
            answered_ids.append(tmpID)
            data["answered_id"] = answered_ids  # Overwrite with new ID
            with open(self.min_next_post_file, "w") as f:
                json.dump(data, f, indent=2)
            print(f"Added {tmpID} for next iterations")
            return False

    # for now just processes "main" posts, i.e. not replies/follow-ups
    def process_posts(self):

        try:
            # get last post
            topic = self.get_last_post()
            #active_posts = self.get_active_posts()
            #print(active_posts.keys())
        except Exception as e:
            print("ERROR - Failed to parse feed due to the following exception:")
            print(str(e))
            return

        if self.checkAnswerExist(topic['id']) :
             # no need to answer someone already answered
             print('no need to answer someone already answered')
        else:
            # otherwise, process it
            try:
                answer, post_str = self.ai_wrapper(topic)
                print('topic',topic,' \n ANSWER: ',answer)
                postedMM = self.post_response(answer)
                post_str = self.write_min_next_post(topic['id'])

            except Exception as e:
                print(f"ERROR - Failed to process post {topic['id']} due to the following exception:")
                print(str(e))
