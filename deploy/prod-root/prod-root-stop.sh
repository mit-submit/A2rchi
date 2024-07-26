#!/bin/bash

if [ -z "$(ls -A A2rchi-prod-root/deploy/prod-root/)" ]; then
    echo "Deployment directory is empty; skipping docker compose down!"
else
    echo "Stop running docker compose!"
    cd A2rchi-prod-root/deploy/prod-root/
    docker compose -f prod-root-compose.yaml down
fi
