#!/bin/bash

if [ -z "$(ls -A A2rchi-dev/deploy/dev/)" ]; then
    echo "Deployment directory is empty; skipping docker compose down"
else
    echo "Stop running docker compose"
    cd A2rchi-dev/deploy/dev/
    docker compose -f dev-compose.yaml down
fi
