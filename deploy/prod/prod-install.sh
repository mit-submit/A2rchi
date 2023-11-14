#!/bin/bash

# create volume if it doesn't already exist
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-data`
if [[ $exists != 'a2rchi-prod-data' ]]; then
    docker volume create --name a2rchi-prod-data
fi

# create volume if it doesn't already exist for postgres data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-pg-data`
if [[ $exists != 'a2rchi-prod-pg-data' ]]; then
    docker volume create --name a2rchi-prod-pg-data
fi

# create volume if it doesn't already exist for grafana data
exists=`docker volume ls | awk '{print $2}' | grep a2rchi-prod-grafana-data`
if [[ $exists != 'a2rchi-prod-grafana-data' ]]; then
    docker volume create --name a2rchi-prod-grafana-data
fi

# fill-in variables in grafana files
export grafanapass=`cat A2rchi-prod/deploy/prod/secrets/grafana_password.txt`
sed -i 's/GRAFANA_PASSWORD/'"${grafanapass}"'/g' A2rchi-prod/deploy/grafana/datasources.yaml
sed -i 's/GRAFANA_PASSWORD/'"${grafanapass}"'/g' A2rchi-prod/deploy/init.sql
sed -i 's/RUNTIME_ENV/prod/g' A2rchi-prod/deploy/grafana/datasources.yaml
unset grafanapass

# build base image; try to reuse previously built image
cd A2rchi-prod/deploy/prod/
docker build -f ../dockerfiles/Dockerfile-base -t a2rchi-base:BASE_TAG ../..

# start services
echo "Starting docker compose"
docker compose -f prod-compose.yaml up -d --build --force-recreate --always-recreate-deps

# # secrets files are created by CI pipeline and destroyed here
# rm secrets/*.txt
