#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod-meta/deploy/prod-meta/
docker compose -f prod-compose.yaml down
