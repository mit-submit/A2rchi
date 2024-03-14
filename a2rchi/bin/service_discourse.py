from fluent_discourse import Discourse

import json
import os
import requests
import time
from markdownify import markdownify as md

from a2rchi.utils.env import read_secret



###################  READ ME ########################################
###  The code points to two different systems: the ROOT forum Discourse interface (https://test-root-forum.webtest.cern.ch/), for now a test platform on which ludo is admin and has api key; and the CERN mattermost channel (https://mattermost.web.cern.ch/a2rchi/channels/town-square) on which we activated webhooks (MATTERMOST_WEBHOOK) and a private access key (PAK), which is stored as a secret on github "MM_PAK". The discourse key is stored as secret on github "DISCOURSE_KEY". To send a request the sending machine needs to be in the CERN network, hence it should be run from lxplus (CERN's computing framework). Core of this script is the function process_posts, see below.
### 
###  WHAT STILL NEEDS TO BE DONE: connecting the actual chain, making sure the format of the conversation history, as taken in input by the chain, is correct. Adding the piece to retrieve separately from the chain relevant forum posts (I would suggest feeding A2rchi just with docs and tutorials for now) in order to provide root hosts with similar issues that already have an answer. Find a practical way to implement a question cap. Maybe connect to database for monitoring 
###
###


# DEFINITIONS
MATTERMOST_HEADERS = {'content-type': 'application/json'}
ACTIVE_POSTS_FILE = '~/data/active_posts.json'


# set openai
# os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
# os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")
mattermost_url = 'https://mattermost.web.cern.ch/' 
mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
mattermost_channel_id = '1p3g6kg19fyzxezats3wyze6oy'
PAK = read_secret("MATTERMOST_PAK")


discourse_url = 'https://test-root-forum.webtest.cern.ch/'# read_secret("DISCOURSE_URL")
discourse_user = 'lumori' #read_secret("DISCOURSE_USER")
discourse_key = read_secret("DISCOURSE_KEY")

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
    #remove all posts from MM channel
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
        # get all the latest topics (i.e. threads made of posts by different users or hosts) from the Discourse forum 
        feed = client.latest.json.get()["topic_list"]["topics"]
        topic_ids = list(map(lambda topic: topic['id'], feed))
    except Exception as e:
        print("ERROR - Failed to parse feed due to the following exception:")
        print(str(e))
        # time.sleep(60)
        return active_posts

    for topic in feed:
        if topic["reply_count"]!=0 ^ topic["id"] in active_posts.keys(): 
            # cases in which you don't need any processing to take place: if the topic already has a reply on Discourse that is not out on the MM channel (hence a2rchi does not need to give an answer), or if there is no answer yet on Discourse but the topic has already a suggested answer on MM (ie a2rchi looked at the topic already and made a proposal)
            continue
        elif topic["reply_count"]!=0 and topic["id"] in active_posts.keys():
            # reply has been published on Discourse: remove suggestion from MM
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
                answers+=1 #maybe this needs a cleanup
                note = ""

                # TODO : perform a separate semantic search in the forum database to spot similar forum posts that already have an answer
                if False: note="Found following related topics in the forum:"
                
                
                response = f"====================\nReplying to Post @{topic['id']}\n==========\n\nurl: {discourse_url}/t/{topic['id']}\n==========\n\n==========\n\n{md(post_str)}\n==========\n\nA2RCHI RESPONSE: {response}\n====================\n"
                
                active_posts[topic['id']] = response
                post_response(response,topic["id"])
                
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

answers = 0
cap = 100

while answers<cap:
    # break
    active_posts = process_posts(active_posts)
    print(active_posts.keys())

    # sleep for 60s
    time.sleep(60)

post_response("Maximum A2rchi messages exceeded! Please check for new forum posts on https://test-root-forum.webtest.cern.ch/")
