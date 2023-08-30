#!/bin/bash
#---------------------------------------------------------------------------------------------------
# Install the A2rchi config.
#---------------------------------------------------------------------------------------------------
# Configure and install

# generate the setup file
rm -f setup.sh
touch setup.sh

# Where are we?
HERE=`pwd`

# This is the full setup.sh script
echo "# DO NOT EDIT !! THIS FILE IS GENERATED AT INSTALL (install.sh) !!
export A2RCHI_BASE=$HERE
export OPENAI_API_KEY=\`cat $HOME/.openai/api.key\`
export PATH=\${PATH}:\${A2RCHI_BASE}/bin
export PYTHONPATH=\${PYTHONPATH}:\${A2RCHI_BASE}/interfaces:\${PYTHONPATH}:\${A2RCHI_BASE}/utils

source $HOME/.cleo
source $HOME/.imap
source $HOME/.sender
source $HOME/.salt
" > ./setup.sh
