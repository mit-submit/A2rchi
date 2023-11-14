#!/bin/bash

if [ -z "$(ls -A A2rchi-prod-meta/deploy/prod-meta/)" ]; then
    echo "Deployment directory is empty; skipping docker compose down"
else
    echo "Stop running docker compose"
    cd A2rchi-prod-meta/deploy/prod-meta/
    docker compose -f prod-meta-compose.yaml down
fi
