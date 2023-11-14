#!/bin/bash

# create volume if it doesn't already exist
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-801-data`
if [[ $exists != 'a2rchi-prod-801-data' ]]; then
    docker volume create --name a2rchi-prod-801-data
fi

# create volume if it doesn't already exist for postgres data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-801-pg-data`
if [[ $exists != 'a2rchi-prod-801-pg-data' ]]; then
    docker volume create --name a2rchi-prod-801-pg-data
fi

# create volume if it doesn't already exist for grafana data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-801-grafana-data`
if [[ $exists != 'a2rchi-prod-801-grafana-data' ]]; then
    docker volume create --name a2rchi-prod-801-grafana-data
fi

# fill-in variables in grafana files
export grafanapass=`cat A2rchi-prod-801/deploy/prod-801/secrets/grafana_password.txt`
sed -i 's/GRAFANA_PASSWORD/'"${grafanapass}"'/g' A2rchi-prod-801/deploy/grafana/datasources.yaml
sed -i 's/GRAFANA_PASSWORD/'"${grafanapass}"'/g' A2rchi-prod-801/deploy/init.sql
sed -i 's/RUNTIME_ENV/prod-801/g' A2rchi-prod-801/deploy/grafana/datasources.yaml
unset grafanapass

# build base image; try to reuse previously built image
cd A2rchi-prod-801/deploy/prod-801/
docker build -f ../dockerfiles/Dockerfile-base -t a2rchi-base:BASE_TAG ../..

# start services
echo "Starting docker compose"
docker compose -f prod-801-compose.yaml up -d --build --force-recreate --always-recreate-deps

# # secrets files are created by CI pipeline and destroyed here
# rm secrets/*.txt
