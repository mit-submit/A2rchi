#!/bin/bash

echo "Stop running docker compose"
cd A2rchi-prod-ROOT/deploy/prod-ROOT/
docker compose -f prod-ROOT-compose.yaml down