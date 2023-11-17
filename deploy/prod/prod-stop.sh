#!/bin/bash

if [ -z "$(ls -A A2rchi-prod/deploy/prod/)" ]; then
    echo "Deployment directory is empty; skipping docker compose down"
else
    echo "Stop running docker compose"
    cd A2rchi-prod/deploy/prod/
    docker compose -f prod-compose.yaml down
fi
