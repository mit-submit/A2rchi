#!/bin/bash

# tmux new-session -d -s chat-service './deploy/run_chat.sh'
# tmux new-session -d -s cleo-service './deploy/run_cleo.sh'
# tmux new-session -d -s mailer-service './deploy/run_mailbox.sh'
# tmux new-session -d -s scraper-service './deploy/run_scraper.sh'



echo "Going into A2rchi directory"
cd A2rchi
echo "Starting docker compose"
echo $1 # | docker secret create my_external_secret -
# docker compose up -d
