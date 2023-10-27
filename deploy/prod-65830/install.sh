#!/bin/bash

# create volume if it doesn't already exist
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-piazza-data`
if [[ $exists != 'a2rchi-piazza-data' ]]; then
    docker volume create --name a2rchi-piazza-data
fi

# start services
echo "Starting docker compose"
cd deploy/prod-65830/
docker compose -f compose.yaml up -d --build --force-recreate --always-recreate-deps
