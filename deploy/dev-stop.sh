#!/bin/bash

echo "Stop running docker compose"
cd A2rchi/deploy/
docker compose down -f dev-compose.yaml
