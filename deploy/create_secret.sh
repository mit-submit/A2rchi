#!/bin/bash

secret_name=$1
secret_file=$2
env=$3

touch ${{ github.workspace }}/deploy/"${env}"/secrets/"${secret_file}"
echo "${{ secrets.${secret_name} }}" >> ${{ github.workspace }}/deploy/"${env}"/secrets/"${secret_file}"
chmod 400 ${{ github.workspace }}/deploy/"${env}"/secrets/"${secret_file}"
