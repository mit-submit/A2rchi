#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod-801/deploy/prod-801/
docker compose -f prod-801-compose.yaml down
