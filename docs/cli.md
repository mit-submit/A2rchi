# The A2rchi CLI

This work adds an initial CLI which can be used to `create`, `update`, and `delete` instances of an a2rchi deployment. 

**Note** that before you begin you must also have `docker` and `docker compose` installed on the machine you're using in order for the CLI to work (it acts as a wrapper around `docker compose` commands).

## Install

To install the CLI, activate a virtual/conda environment and from the root of the repository (i.e. where the `pyproject.toml` file is) run:
```
$ pip install .
```

This will install A2rchi's dependencies as well as a local CLI tool. You should be able to see that it is installed using the `which` command (your output will be slightly different):
```
$ which a2rchi
/Users/juliush/A2rchi/venv/bin/a2rchi
```

## Overview

The CLI has the following commands:
```
a2rchi create --name <name> --a2rchi-config <path-to-config> [--grafana True]
a2rchi delete --name <name>
a2rchi update --name <name> --a2rchi-config <path-to-config> #NOTE SURE IF THIS WORKS CURRENTL
```

The `--name` represents the name of your a2rchi deployment and the files for your deployment will be stored under `~/.a2rchi/a2rchi-{name}` on your local machine (you don't need to do anything with them, this is just an FYI). The `--grafana` flag can be included with the value `True` in order to include a Grafana dashboard with your deployment. The `--a2rchi-config` is a config provided by the user which can override any of the templatable fields in `a2rchi/templates/base-config.yaml` (in the future, we could make them all templatable). For example, I have been using the following config to experiment with using GPT-3.5 for both condensing and answering questions:
```
# stored in file example_conf.yaml
name: all_gpt35_config #REQUIRED

global:
  TRAINED_ON: "your data" #REQUIRED

chains:
  input_lists: #REQUIRED
    - config_old/submit.list
    - config_old/miscellanea.list
  chain:
    MODEL_NAME: OpenAIGPT35
    CONDENSE_MODEL_NAME: OpenAIGPT35
    SUMMARY_MODEL_NAME: OpenAIGPT35
  prompts:
    CONDENSING_PROMPT: config_old/prompts/condense.prompt #REQUIRED
    MAIN_PROMPT: config_old/prompts/submit.prompt #REQUIRED
    SUMMARY_PROMPT: config_old/prompts/summary.prompt #REQUIRED
```

## Create new instance

Now, to create an instance of an A2rchi deployment called `my-a2rchi` (with a grafana dashboard), simply create a file called `example_conf.yaml` with the contents above and run:
```
$ a2rchi create --name my-a2rchi --a2rchi-config example_conf.yaml --grafana True
```

It will take up to (a few) seconds(s) for the command to finish (and possibly longer the first time you run it b/c `docker` will have to fetch the container images from Docker Hub), but you should ultimately see output similar to:
```
[a2rchi]>> Creating docker volumes
[a2rchi]>> Creating docker volume: a2rchi-my-a2rchi
[a2rchi]>> Creating docker volume: a2rchi-pg-my-a2rchi
[a2rchi]>> Creating docker volume: a2rchi-grafana-my-a2rchi
[a2rchi]>> Preparing Grafana
[a2rchi]>> Preparing Postgres
[a2rchi]>> Preparing Compose
[a2rchi]>> Starting docker compose
...
... A lot of logs from pulling and extracting images ...
...
Network a2rchi-my-a2rchi_default  Creating
Network a2rchi-my-a2rchi_default  Created
Container postgres-my-a2rchi  Creating
Container chromadb-my-a2rchi  Creating
Container postgres-my-a2rchi  Created
Container chromadb-my-a2rchi  Created
Container grafana-my-a2rchi  Creating
Container chat-my-a2rchi  Creating
Container grafana-my-a2rchi  Created
Container chat-my-a2rchi  Created
Container postgres-my-a2rchi  Starting
Container chromadb-my-a2rchi  Starting
Container chromadb-my-a2rchi  Started
Container postgres-my-a2rchi  Started
Container chromadb-my-a2rchi  Waiting
Container postgres-my-a2rchi  Waiting
Container postgres-my-a2rchi  Waiting
Container postgres-my-a2rchi  Healthy
Container grafana-my-a2rchi  Starting
Container postgres-my-a2rchi  Healthy
Container grafana-my-a2rchi  Started
Container chromadb-my-a2rchi  Healthy
Container chat-my-a2rchi  Starting
Container chat-my-a2rchi  Started
```

You can verify that all your images are up and running properly by executing the following:
```
$ docker ps -a
CONTAINER ID   IMAGE                          COMMAND                  CREATED         STATUS                   PORTS                    NAMES
7fd9015bf5df   mdr223/a2rchi:chat-0.0.1       "python -u a2rchi/bi…"   3 minutes ago   Up 2 minutes             0.0.0.0:7861->7861/tcp   chat-my-a2rchi
d1f749f12416   mdr223/a2rchi:grafana-0.0.1    "/run.sh"                3 minutes ago   Up 2 minutes             0.0.0.0:3000->3000/tcp   grafana-my-a2rchi
f1298d6efefc   postgres:16                    "docker-entrypoint.s…"   3 minutes ago   Up 2 minutes (healthy)   5432/tcp                 postgres-my-a2rchi
efcb7be30a6e   mdr223/a2rchi:chromadb-0.0.1   "uvicorn chromadb.ap…"   3 minutes ago   Up 2 minutes (healthy)   0.0.0.0:8000->8000/tcp   chromadb-my-a2rchi
```

## Update existing instance (not sure this still works...)

Now, suppose you wanted to re-configure your deployment to use GPT-4 instead, you could accomplish this by modifying `example_conf.yaml` to have the following:
```
# stored in file example_conf.yaml
config_name: all_gpt4_config
...
model_name: OpenAIGPT4
condense_model_name: OpenAIGPT4
summary_model_name: OpenAIGPT4
...
```
And then update the deployment in place as follows:
```
$ a2rchi update --name my-a2rchi
[a2rchi]>> Updating config
[a2rchi]>> config updated successfully w/config_id: 3
```
If you check the logs for `chat-my-a2rchi` (by using `docker logs chat-my-a2rchi -f`) you should see that it will restart with GPT-4 as its model instead.

**Note that you could also update your deployments prompts in place by, e.g., creating a new `main.prompt` and providing the filepath to this new prompt in your `example_conf.yaml` (it would also work if you just updated the prompt(s) in-place and kept their filepath(s) as-is in the config file).**

## Removing deployment

Finally, to tear down the deployment, simply run:
```
$ a2rchi delete --name my-a2rchi
```