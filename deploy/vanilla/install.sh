#!/bin/bash

# create volume if it doesn't already exist
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-data`
if [[ $exists != 'a2rchi-data' ]]; then
    docker volume create --name a2rchi-data
fi

# start services
echo "Starting docker compose"
cd deploy/vanilla/
docker compose -f compose.yaml up -d --build --force-recreate --always-recreate-deps
