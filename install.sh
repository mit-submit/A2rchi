#!/bin/bash
#---------------------------------------------------------------------------------------------------
# Install the basic A2rchi version with a chatbot only using openai!
#
# - we assume you are in the working directory where A2rchi should be installed.
#---------------------------------------------------------------------------------------------------
usage=" $0 [ Any additional variables for a2rchi create command line. ]"

# Additional argument for a2rchi create to make it more than minimal
ADDITONAL_ARGS=$*
echo "Additional Args: $ADDITONAL_ARGS"

# Install the package

#   check whether containers are running
test=`podman ps -a --format "{{.Names}}" | grep a2my`
if ! [ -z "$test" ]
then
    echo " ERROR - containers or images with name a2my are running."
    echo " - usage: $usage"
    exit 1
else
    echo " INFO - No running containers found."
fi

if [ -d "A2rchi" ]
then
    echo " ERROR - A2rchi directory exists, please correct."
    echo " - usage: $usage"
    exit 1
fi

Find=`ls -a ~/.a2rchi | grep a2my`
if ! [ -z "$Find" ]
then
    echo " ERROR - File with name a2my exists in home directory."
    echo " - usage: $usage"
    exit 1
fi

git clone https://github.com/Markus-Paus/A2rchi
cd A2rchi

# Configure and install containers
if [ -z `which podman` ]
then
    echo " ERROR - podman is not installed. Please, install first."
    echo " - usage: $usage"
    exit 0
fi
if [ -z `which podman-compose` ]
then
    echo " ERROR - podman-compose is not installed. Please, install first."
    echo " - usage: $usage"
    exit 1
fi

#   initialize virtua envirnment for python and a2rchi 
python -m venv myenv
source myenv/bin/activate
pip install .

#   some variables we will need
export SECRET_BASE=~/.a2rchi/base
export GRAFANA_PG_PASSWORD=a2-postgress

#   make sure passwords are available (this directory needs to be consistent with your config)
if [ "$dir" != "A2rchi" ]
then
    mkdir -p $SECRET_BASE
fi
echo $GRAFANA_PG_PASSWORD > $SECRET_BASE/pg_password.txt # you can use any password
if ! [ -f ~/.openai/api.key ]
then
    echo " ERROR - you must have an openai api-key at: ~/.openai/api.key "
    echo " - usage: $usage"
    exit 1
fi
#   this will always update to your present api key
cp ~/.openai/api.key $SECRET_BASE/openai_api_key.txt

#   edit/create your configs (yaml file and maybe a bunch of list files to be added to the yaml)
if ! [ -f configs/my.yaml ]
then
    cp configs/example_conf.yaml configs/my.yaml
    cp configs/submit.list       configs/my.list
    #emacs -nw configs/my.{yaml,list}
fi
    
#   now install (this will need to create conda etc.. so it can take some time the first time)
a2rchi create --name a2my --a2rchi-config ./configs/my.yaml --podman $ADDITIONAL_ARGS
    
# show the containers running
podman ps
