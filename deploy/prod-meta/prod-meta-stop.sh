#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod-meta/deploy/prod-meta/
docker compose -f prod-meta-compose.yaml down
