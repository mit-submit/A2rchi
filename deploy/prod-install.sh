#!/bin/bash

# create volume if it doesn't already exist
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-data`
if [[ $exists != 'a2rchi-prod-data' ]]; then
    docker volume create --name a2rchi-prod-data
fi

# start services
echo "Starting docker compose"
cd A2rchi/deploy/
docker compose up -f prod-compose.yaml -d --build --force-recreate --always-recreate-deps

# secrets files are created by CI pipeline and destroyed here
rm cleo_*.txt
rm imap_*.txt
rm sender_*.txt
rm flask_uploader_app_secret_key.txt
rm uploader_salt.txt
rm openai_api_key.txt
