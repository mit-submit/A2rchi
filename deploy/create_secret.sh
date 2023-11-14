#!/bin/bash

secret_file=$1
secret=$2

touch WORKSPACE/deploy/ENV/secrets/"${secret_file}"
echo "${secret}" >> WORKSPACE/deploy/ENV/secrets/"${secret_file}"
chmod 400 WORKSPACE/deploy/ENV/secrets/"${secret_file}"
