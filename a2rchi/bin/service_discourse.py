from fluent_discourse import Discourse

import json
import os
import requests
import time
from markdownify import markdownify as md


# DEFINITIONS
MATTERMOST_HEADERS = {'content-type': 'application/json'}

# set openai
# os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
# os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
mattermost_url = 'https://mattermost.web.cern.ch/' #read_secret("MATTERMOST_WEBHOOK")
mattermost_webhook = 'https://mattermost.web.cern.ch/hooks/3o1js33iqfgsjr8cxu915mw1ie'
mattermost_channel_id = '1p3g6kg19fyzxezats3wyze6oy'
PAK = "czazsdrkq7ndfcdqrspe34666o"


discourse_url = 'https://test-root-forum.webtest.cern.ch/'# read_secret("DISCOURSE_URL")
discourse_user = 'lumori' #read_secret("DISCOURSE_USER")
discourse_key = 'acbe06fbbb5d0facf301afa5df8667e0f6c6328657ee1e7515016b9057d52396' #read_secret("DISCOURSE_KEY")

# scrape data onto the filesystem
# scraper = Scraper()
# scraper.hard_scrape(verbose=True)

# # update vector store
# data_manager = DataManager()
# data_manager.update_vectorstore()


client = Discourse(base_url=discourse_url, username=discourse_user, api_key=discourse_key, raise_for_rate_limit=True)


# create chain
# a2rchi_chain = Chain()

def _call_chain(chain, post):
    # convert post --> history
    post_str = "SUBJECT: " + post['history'][-1]['subject'] + "\n\nCONTENT: " + post['history'][-1]['content']
    history = [("User", post_str)]
    return chain(history)['answer'], post_str

def call_basic_chain(post):
    return "42", post


def post_response(response,id):
    # send response to MM
    r = requests.post(mattermost_webhook, data=json.dumps({"text": response,"channel" : "town-square","root_id": id}), headers=MATTERMOST_HEADERS)
    print(r)
    return

def remove_response(id):
    # send response to MM
    content = "api/v4/posts/" + id
    r = requests.delete(mattermost_url + content, headers= {"Authorization" : f"Bearer {PAK}"})
    print(r)
    return

def clear_channel():
    content = f"api/v4/channels/{mattermost_channel_id}/posts"
    r = requests.get(mattermost_url + content, headers= {"Authorization" : f"Bearer {PAK}"})
    for id in r.json()["order"]:
        remove_response(id)
    return

def get_active_posts():
    content = f"api/v4/channels/{mattermost_channel_id}/posts"
    r = requests.get(mattermost_url + content, headers= {"Authorization" : f"Bearer {PAK}"})
    active_posts={}
    for id in r.json()["order"]:
        active_posts[id]=r.json()["posts"][id]["message"]
    return active_posts


def process_posts(active_posts):
    try:
        feed = client.latest.json.get()["topic_list"]["topics"]
        topic_ids = list(map(lambda topic: topic['id'], feed))
    except Exception as e:
        print("ERROR - Failed to parse feed due to the following exception:")
        print(str(e))
        # time.sleep(60)
        return active_posts

    for topic in feed:
        if topic["reply_count"]!=0 ^ topic["id"] in active_posts.keys(): 
            continue
        elif topic["reply_count"]!=0 and topic["id"] in active_posts.keys():
            active_posts.pop(topic["id"])
            remove_response(topic["id"])
            print("removed post ", topic["id"])
            continue
        else:
            try:
                # otherwise, process it
                post = client.t[topic["id"]].json.get()["post_stream"]["posts"][0]["cooked"]

                print(f"PROCESSING NEW POST: {topic['id']}")
                response, post_str = call_basic_chain(post)
                answers+=1
                note = ""
                if False: note="Found following related topics in the forum:"
                response = f"====================\nReplying to Post @{topic['id']}\n==========\n\nurl: {discourse_url}/t/{topic['id']}\n==========\n\n==========\n\n{md(post_str)}\n==========\n\nA2RCHI RESPONSE: {response}\n====================\n"
                
                active_posts[topic['id']] = response
                post_response(response,topic["id"])
                
            except Exception as e:
                print(f"ERROR - Failed to process post {topic['id']} due to the following exception:")
                print(str(e))
                
    return active_posts


clear_channel()
active_posts = get_active_posts()

answers = 0
cap = 10

while answers<cap:
    active_posts = process_posts(active_posts)
    print(active_posts.keys())

    # sleep for 60s
    time.sleep(1)

post_response("Maximum A2rchi messages exceeded! Please check for new forum posts on https://test-root-forum.webtest.cern.ch/")
