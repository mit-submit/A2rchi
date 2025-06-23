# Getting Started

A2rchi is an AI Augmented Research Chat Intelligence, originally built for the subMIT project at the MIT physics department. 

At it's core, A2rchi is a RAG chat model which takes in a corpus of data and returns natural language output. However, there are a couple of things that make the A2rchi project unique and specialized toward research and education:

- Along with a native chat interface, A2rchi comes shipped with various other interfaces that enable it to do things such as: give suggestions for answers on a student help forum and help tech support teams answers emails and create tickets.

- A2rchi's fully customizable configuration allows users to tweak and adjust everything from prompts, to context lengths, to documents uploaded, etc. and is easy to deploy and modify.

## System Requirements

A2rchi is containized and is deployed with a python-based CLI. Therefore, it requires:

- `docker` version 24+ (for containers)

- `python 3.10.0+` (for CLI)

## Install

A2rchi's CLI can be used to `create` and `delete` instances of an a2rchi deployment and provides the installation needed for A2rchi. 

To install the CLI, first clone the a2rchi repo:
```
$ git clone https://github.com/mit-submit/A2rchi.git
```
Then, activate a virtual/conda environment and from the root of the repository (i.e. where the `pyproject.toml` file is) by running:
```
$ pip install .
```
This will install A2rchi's dependencies as well as a local CLI tool. You should be able to see that it is installed using the `which` command (your output will be slightly different):
```
$ which a2rchi
/Users/default_user/A2rchi/venv/bin/a2rchi
```

## Secrets

Secrets are values which are sensitive and therefore should not be directly included in code or configuration files. They typically include passwords, API keys, etc. 

To manage these secrets, we ask that you write them to a location on your file system in `.txt` files titled the name of the secrets. You will then give the location of the folder to the configuration file (see next section). You may also use multiple different folders/locations and supply them all to the configuration.

The secrets you are required to have to start a2rchi are:
- `openai_api_key`: the API key given by openAI
- `anthropic_api_key`: the API key given by anthropic
- `hf_token`: the API key to your huggingface account,
- `pg_password`: some password you pick which encrypts the database.
We will change imminently as we support open LLMs and it shouldn't be necessary to have all of these for any given deployment. For now, for any api keys you are not using, please still create the corresponding file and write any dummy text.


## Basic CLI Overview

A2rchi's CORE launches its chat interface and data management interface. The CORE CLI has the following commands:
```
a2rchi create --name <name> --a2rchi-config <path-to-config>
a2rchi delete --name <name>
```

The `--name` represents the name of your a2rchi deployment and by default the files for your deployment will be stored under `~/.a2rchi/a2rchi-{name}` on your local machine (you don't need to do anything with them, this is just an FYI).

The `--a2rchi-config` is a configuration file provided by the user which can override any of the templatable fields in `a2rchi/templates/base-config.yaml`. 

### Required configuration fields

There are a few required fields that must be included in every configuration. They are:

1. **`name`**: The name of the configuration (NOTE: this is not neccessarily the name of the a2rchi deployment described above)

2. **`global:TRAINED_ON`**: A quick couple words describing the data that you want A2rchi to specialize in. For example, "introductory classical mechanics" or "the subMIT cluster at MIT."

3. **`chains:input_lists`**: A list of file(s), each containing a list of websites seperated by new lines, used for A2rchi's starting context (more can be uploaded later). For example, `configs/miscellanea.list` contains information of the MIT proffessors who started the A2rchi project:

```
# web pages of various people
https://people.csail.mit.edu/kraska
https://physics.mit.edu/faculty/christoph-paus
```

4. **`chains:prompts:CONDENSING_PROMPT`**: A condensing prompt is a prompt used to condense a chat history and a follow up question into a stand alone question. This configuration line gives the path, relative to the root of the repo, of a file containing a condensing prompt. All condensing prompts must have the following tags in them, which will be filled with the appropriate information: `{chat_history}` and `{question}`. A very general prompt for condensing histories can be found at `configs/prompts/condense.prompt`, so for base installs it will not need to be modified. 

5. **`chains:prompts:SUMMARY_PROMPT`**: #TODO: I don't actually know what this does... For now just link it a blank file....

6. **`chains:prompts:MAIN_PROMPT:`**: A main prompt is a prompt used to qurery LLM with appropriate context and question. This configuration line gives the path, relative to the root of the repo, of a file containing a main prompt. All main prompts must have the following tags in them, which will be filled with the appropriate information: `{question}` and `{context}`. An example prompt specific to subMIT can be found here: `configs/prompts/submit.prompt` (it will not perform well for other applications where it is recommeneded to write your own prompt and change it in the config)

7. **`chains:chain:MODEL_NAME`**: Model name for the choice of LLM (OpenAIGPT4, OpenAIGPT35, AnthropicLLM, DumbLLM, etc)

8. **`chains:chain:CONDENSE_MODEL_NAME`**: Model name for condensing chat histories.

9. **`chains:chain:SUMMARY_MODEL_NAME`**: Model name for summarizing.

10. **`location_of_secrets`**: A list of the absolute paths of folders containing secrets (passwords, API keys, etc.), discussed explicitly in the previous section. 

Below is an example of a bare minimum condifiguration file:
```
# stored in file example_conf.yaml
name: bare_minimum_configuration #REQUIRED

global:
  TRAINED_ON: "subMIT and the people who started A2rchi" #REQUIRED

chains:
  input_lists: #REQUIRED
    - config_old/submit.list
    - config_old/miscellanea.list
  chain:
    - MODEL_NAME: OpenAIGPT4 #REQUIRED
    - CONDENSE_MODEL: OpenAIGPT4 #REQUIRED
    - SUMMARY_MODEL_NAME: OpenAIGPT4 #REQUIRED
  prompts:
    CONDENSING_PROMPT: config_old/prompts/condense.prompt #REQUIRED
    MAIN_PROMPT: config_old/prompts/submit.prompt #REQUIRED
    SUMMARY_PROMPT: config_old/prompts/summary.prompt #REQUIRED

location_of_secrets: #REQUIRED
  - ~/.secrets/a2rchi_base_secrets
```
To view the full list of configuration variables, please refer to the users guide. 

### Create new instance

Now, to create an instance of an A2rchi deployment called `my-a2rchi`, simply create a file called `example_conf.yaml` with the contents like the ones above and run:
```
$ a2rchi create --name my-a2rchi --a2rchi-config example_conf.yaml
```

It will take up to (a few) seconds(s) for the command to finish (and possibly longer (minutes or dozens of minutes) the first time you run it b/c `docker` will have to build the container images from scratch), but you should ultimately see output similar to:
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

To access the chat interface, visit its corresponding port (` 0.0.0.0:7861` in the above example )

### Removing deployment

Lastly, to tear down the deployment, simply run:
```
a2rchi delete --name my-a2rchi
```
You can use the `--rmi` option to remove the images,
```
a2rchi delete --name my-a2rchi --rmi
```

## Helpful Notes for Production Deployments

You may wish to use the CLI in order to stage production deployments. This section covers some useful notes to keep in mind.

### Running multiple deployments on the same machine

The CLI is built to allow multiple deployments to run on the same daemon. The docker networks between all the deployments are seperate, so there is very little risk of them accidentally communicating with one another.

However, one thing to be careful of is the external ports. Suppose you're running two deployments and both of them are running the chat on port 8000. There is no way to view both deployments at the same time from the same port, so instead you should split to forwarding the deployments to other external ports. Generally, this can be done in the configuration:
```
interfaces:
  chat_app:
    EXTERNAL_PORT: 1000
  uploader_app:
    EXTERNAL_PORT: 1001
  grafana:
    EXTERNAL_PORT: 1002

utils:
  data_manager:
    chromadb_external_port: 1050
```

### Persisting data between deployments

Docker volumes persist between deployments, so if you deploy an instance, and upload some further documents, you will not need to redo so every time you deploy. Of course, if you are editing any data, you should explicitly remove this infromation from the volume, or simply remove the volume itself with
```
docker volume rm <volume name>
```
