#!/bin/python
import multiprocessing as mp
import os
import time
from threading import Thread

from src.interfaces import mattermost
from src.utils.env import read_secret
from src.utils.logging import setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory

# set basicConfig for logging
setup_logging()

def run_polling(mattermost_agent, update_time):
    while True:
        mattermost_agent.process_posts()
        time.sleep(update_time)

def main():
    # set openai
    os.environ['OPENAI_API_KEY'] = read_secret("OPENAI_API_KEY")
    os.environ['ANTHROPIC_API_KEY'] = read_secret("ANTHROPIC_API_KEY")
    os.environ['HUGGING_FACE_HUB_TOKEN'] = read_secret("HUGGING_FACE_HUB_TOKEN")

    time.sleep(30) # temporary hack to prevent mattermost from starting at the same time as other services; eventually replace this with more robust solution

    # Initialize Postgres config service (required before any get_full_config() call)
    factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
    PostgresServiceFactory.set_instance(factory)

    # Start webhook server first — its __init__ initializes the config service via MattermostAIWrapper
    print("Initializing Mattermost webhook server")
    webhook_server = mattermost.MattermostWebhookServer()

    # Start polling loop in background thread if PAK is available (config service now ready)
    pak = read_secret("MATTERMOST_PAK")
    if pak:
        print("Initializing Mattermost polling service")
        mattermost_agent = mattermost.Mattermost()
        update_time = int(mattermost_agent.mattermost_config.get("update_time", 60))
        polling_thread = Thread(target=run_polling, args=(mattermost_agent, update_time), daemon=True)
        polling_thread.start()
    else:
        print("MATTERMOST_PAK not set — skipping polling mode")

    webhook_server.run(host='0.0.0.0', port=webhook_server.port)

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
