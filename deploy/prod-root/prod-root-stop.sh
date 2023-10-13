#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod-root/deploy/prod-root/
docker compose -f prod-root-compose.yaml down