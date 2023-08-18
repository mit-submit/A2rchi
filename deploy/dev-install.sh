#!/bin/bash

# tmux new-session -d -s chat-service './deploy/run_chat.sh'
# tmux new-session -d -s cleo-service './deploy/run_cleo.sh'
# tmux new-session -d -s mailer-service './deploy/run_mailbox.sh'
# tmux new-session -d -s scraper-service './deploy/run_scraper.sh'



echo "Going into A2rchi directory"
cd A2rchi
echo "Starting docker compose"
ls
cp requirements.txt deploy/cleo/
cp bin/service_cleo.py deploy/cleo/
cd deploy
docker compose up -d

# # secrets file is created by CI pipeline and destroyed here
# rm cleo_url.txt
# rm cleo_user.txt
# rm cleo_pw.txt
# rm cleo_project.txt
