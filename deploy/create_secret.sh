#!/bin/bash

env=$1
secret_file=$2
secret=$3

touch WORKSPACE/deploy/"${env}"/secrets/"${secret_file}"
echo "${secret}" >> WORKSPACE/deploy/"${env}"/secrets/"${secret_file}"
chmod 400 WORKSPACE/deploy/"${env}"/secrets/"${secret_file}"
