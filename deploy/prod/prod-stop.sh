#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod/deploy/prod/
docker compose -f prod-compose.yaml down
