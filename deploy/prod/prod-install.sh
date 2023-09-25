#!/bin/bash

# create volume if it doesn't already exist
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-data`
if [[ $exists != 'a2rchi-prod-data' ]]; then
    docker volume create --name a2rchi-prod-data
fi

# start services
echo "Starting docker compose"
cd A2rchi-prod/deploy/prod/
docker compose -f prod-compose.yaml up -d --build --force-recreate --always-recreate-deps

# # secrets files are created by CI pipeline and destroyed here
# rm secrets/cleo_*.txt
# rm secrets/imap_*.txt
# rm secrets/sender_*.txt
# rm secrets/flask_uploader_app_secret_key.txt
# rm secrets/uploader_salt.txt
# rm secrets/openai_api_key.txt
# rm secrets/hf_token.txt
