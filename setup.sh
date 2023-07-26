#export A2RCHI_BASE=$HOME/Work/A2rchi
export A2RCHI_BASE=/work/submit/mori25/A2rchi
export OPENAI_API_KEY=`cat $HOME/.openai/api.key`
export PATH="${PATH}:${A2RCHI_BASE}/bin"
export PYTHONPATH="${PYTHONPATH}:${A2RCHI_BASE}/interfaces:${PYTHONPATH}:${A2RCHI_BASE}/utils:${A2RCHI_BASE}/utils"

#source $HOME/.cleo
#source $HOME/.imap
#source $HOME/.sender
