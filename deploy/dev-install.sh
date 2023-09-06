#!/bin/bash

echo "Starting docker compose"
cd A2rchi/deploy/
docker compose up -d --build --force-recreate --always-recreate-deps

# secrets files are created by CI pipeline and destroyed here
rm cleo_*.txt
rm imap_*.txt
rm sender_*.txt
rm openai_api_key.txt
