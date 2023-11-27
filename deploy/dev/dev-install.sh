#!/bin/bash

# create volume if it doesn't already exist for app data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-dev-data`
if [[ $exists != 'a2rchi-dev-data' ]]; then
    docker volume create --name a2rchi-dev-data
fi

# create volume if it doesn't already exist for postgres data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-dev-pg-data`
if [[ $exists != 'a2rchi-dev-pg-data' ]]; then
    docker volume create --name a2rchi-dev-pg-data
fi

# create volume if it doesn't already exist for grafana data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-dev-grafana-data`
if [[ $exists != 'a2rchi-dev-grafana-data' ]]; then
    docker volume create --name a2rchi-dev-grafana-data
fi

# fill-in variables in grafana files
export grafanapass=`cat A2rchi-dev/deploy/dev/secrets/grafana_password.txt`
sed -i 's/GRAFANA_PASSWORD/'"${grafanapass}"'/g' A2rchi-dev/deploy/grafana/datasources.yaml
sed -i 's/GRAFANA_PASSWORD/'"${grafanapass}"'/g' A2rchi-dev/deploy/init.sql
unset grafanapass

# build base image; try to reuse previously built image
cd A2rchi-dev/deploy/dev/
docker build -f ../dockerfiles/Dockerfile-base -t a2rchi-base:BASE_TAG ../..

# start services
echo "Starting docker compose"
docker compose -f dev-compose.yaml up -d --build --force-recreate --always-recreate-deps

# # secrets files are created by CI pipeline and destroyed here
# rm secrets/*.txt
