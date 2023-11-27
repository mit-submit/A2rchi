#!/bin/bash

if [ -z "$(ls -A A2rchi-prod-801/deploy/prod-801/)" ]; then
    echo "Deployment directory is empty; skipping docker compose down"
else
    echo "Stop running docker compose"
    cd A2rchi-prod-801/deploy/prod-801/
    docker compose -f prod-801-compose.yaml down
fi
