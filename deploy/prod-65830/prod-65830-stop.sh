#!/bin/bash

if [ -z "$(ls -A A2rchi-prod-65830/deploy/prod-65830/)" ]; then
    echo "Deployment directory is empty; skipping docker compose down"
else
    echo "Stop running docker compose"
    cd A2rchi-prod-65830/deploy/prod-65830/
    docker compose -f prod-65830-compose.yaml down
fi
