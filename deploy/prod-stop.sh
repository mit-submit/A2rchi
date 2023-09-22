#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod/A2rchi/deploy/
docker compose -f prod-compose.yaml down
