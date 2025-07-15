# Getting Started

A2rchi is an AI Augmented Research Chat Intelligence, originally built for the subMIT project at the MIT physics department. 

At it's core, A2rchi is a RAG chat model which takes in a corpus of data and returns natural language output. However, there are a couple of things that make the A2rchi project unique and specialized toward research and education:

- Along with a native chat interface, A2rchi comes shipped with various other interfaces that enable it to do things such as: give suggestions for answers on a student help forum and help tech support teams answers emails and create tickets.

- A2rchi's fully customizable configuration allows users to tweak and adjust everything from prompts, to context lengths, to documents uploaded, etc. and is easy to deploy and modify.

## System Requirements

A2rchi is containized and is deployed with a python-based CLI. Therefore, it requires:

- `docker` version 24+ or `podman` version 5.4.0+ (for containers)
- `python 3.10.0+` (for CLI)

Note: If you plan to run open source models on your machine's GPUs, please check out the `User Guide` for more information.

## Install

A2rchi's CLI can be used to `create` and `delete` instances of an a2rchi deployment and provides the installation needed for A2rchi. 

To install the CLI, first clone the a2rchi repo:
```nohighlight
git clone https://github.com/mit-submit/A2rchi.git
```
Then, activate a virtual/conda environment, and from the root of the repository (i.e., where the `pyproject.toml` file is) run:
```nohighlight
pip install .
```
This will install A2rchi's dependencies as well as a local CLI tool. You should be able to see that it is installed with
```nohighlight
which a2rchi
```
which will show the full path of the executable that is `a2rchi`.

## Secrets

Secrets are values which are sensitive and therefore should not be directly included in code or configuration files. They typically include passwords, API keys, etc.

To manage these secrets, we ask that you write them to a location on your file system in `.txt` files titled the name of the secrets. You will then give the location of the folder to the configuration file (see next section). You may also use multiple different folders/locations and supply them all to the configuration.

The only secret that is required to launch a minimal version of A2rchi (chatbot with open source LLM and embeddings) is:

- `pg_password`: some password you pick which encrypts the database.

If you are not running an open source model, you can use various OpenAI or Anthropic models if you provide the following secrets,

- `openai_api_key`: the API key given by OpenAI
- `anthropic_api_key`: the API key given by Anthropic

respectively. If you want to access private embedding models or LLMs from HuggingFace, you will need to provide the following:

- `hf_token`: the API key to your HuggingFace account

For many of the other services provided by A2rchi, additional secrets are required. These are detailed in User's Guide

## Basic CLI Overview

The nominal setup of A2rchi launches its chat interface and data management interface. The CLI has the following commands:
```nohighlight
a2rchi create --name <name> --a2rchi-config <path-to-config> [OPTIONS]
a2rchi delete --name <name> [OPTIONS]
```

The `--name` represents the name of your a2rchi deployment, and will be used in naming the images, containers, and also volumes associated with the deployment. All files needed to deploy a2rchi will be stored by default under `~/.a2rchi/a2rchi-{name}` on your local machine, where `docker/podman compose` will be run from. Note, you might need to change this, e.g., for permission reasons, in which case simply set the environment variable `A2RCHI_DIR` to the desired path.

The `--a2rchi-config` is a configuration file provided by the user which can override any of the templatable fields in `a2rchi/templates/base-config.yaml`. See more below.

You can see additional options with `a2rchi create --help`, which are also further detailed in the User Guide.

Before we execute the `create` command to launch A2rchi, we must pass a configuration file which has a few requirements.

### Required configuration fields

There are a few required fields that must be included in every configuration. They are:

1. **`name`**: The name of the configuration (NOTE: this is not neccessarily the name of the a2rchi deployment described above).

2. **`global:TRAINED_ON`**: A few words describing the documents you are uploading to A2rchi. For example, "introductory classical mechanics" or "the SubMIT cluster at MIT.".

3. **`location_of_secrets`**: A list of the absolute paths of folders containing secrets (passwords, API keys, etc.), discussed explicitly in the previous section. 

4. **`chains:prompts:MAIN_PROMPT:`**: The main prompt is the prompt used to query LLM with appropriate context and question. This configuration line gives the path, relative to the root of the repo, of a file containing a main prompt. All main prompts must have the following tags in them, which will be filled with the appropriate information: `Question: {question}` and `Context: {context}`. An example prompt specific to subMIT can be found here: `configs/prompts/submit.prompt` (it will not perform well for other applications where it is recommeneded to write your own prompt and change it in the config).

5. **`chains:prompts:CONDENSING_PROMPT`**: A condensing prompt is a prompt used to condense a chat history and a follow up question into a stand alone question. This configuration line gives the path, relative to the root of the repo, of a file containing a condensing prompt. All condensing prompts must have the following tags in them, which will be filled accordingly: `Chat History: {chat_history}` and `Follow Up Input: {question}`. A very general prompt for condensing histories can be found at `configs/prompts/condense.prompt`, so for base installs it will not need to be modified, but it can be adjusted as desired.

6. **`chains:chain:MODEL_NAME`**: Model name for the choice of LLM (OpenAIGPT4, OpenAIGPT35, AnthropicLLM, DumbLLM, HuggingFaceOpenLLM, VLLM). See more in optional configuration fields for how to use non-default HuggingFace or vLLM models.

7. **`chains:chain:CONDENSE_MODEL_NAME`**: Model name for condensing chat histories to a single question. Note, if this is not the same as MODEL_NAME

Below is an example of a bare minimum configuration file:
```
# stored in file example_conf.yaml
name: bare_minimum_configuration #REQUIRED

global:
  TRAINED_ON: "subMIT and the people who started A2rchi" #REQUIRED

locations_of_secrets: #REQUIRED
  - ~/.secrets/a2rchi_base_secrets # in this dir, there should be, e.g., pg_password.txt

chains:
  chain:
    MODEL_NAME: OpenAIGPT4 #REQUIRED
    CONDENSE_MODEL: OpenAIGPT4 #REQUIRED
  prompts:
    MAIN_PROMPT: config_old/prompts/submit.prompt #REQUIRED
    CONDENSING_PROMPT: config_old/prompts/condense.prompt #REQUIRED
```

To view the full list of configuration variables, including how to pass documents for RAG, please refer to the User Guide.

### Create new instance

Now, to create an instance of an A2rchi deployment called `my-a2rchi`, create your config file, e.g., `configs/my_config.yaml`, which includes at least the required fields detailed above, and run:
```nohighlight
a2rchi create --name my-a2rchi --a2rchi-config configs/my_config.yaml --podman
```

The first time you run this command it will take longer than usual (order minutes) because `docker`/`podman` will have to build the container images from scratch, then subsequent deployments will be quicker. Below is an example output from running this minimal configuration on a system that uses `podman` (specified with the `--podman` option as seen in the command just above).
```nohighlight
[a2rchi]>> Creating volumes
[a2rchi]>> Creating volume: a2rchi-my-a2rchi
[a2rchi]>> Creating volume: a2rchi-pg-my-a2rchi
[a2rchi]>> Preparing Postgres
[a2rchi]>> Preparing Compose
[a2rchi]>> Starting compose
...
... Many logs from the compose command (pulling images, building locally, running them, ...)
...
[a2rchi]>> chromadb-my-a2rchi
...
[a2rchi]>> postgres-my-a2rchi
...
[a2rchi]>> chat-my-a2rchi
```

You can verify that all your images are up and running properly in containers by executing the `podman ps` (or `docker ps`) command, and you should see something like:
```nohighlight
CONTAINER ID  IMAGE                              COMMAND               CREATED             STATUS                       PORTS                   NAMES
7e823e15e8d8  localhost/chromadb-my-a2rchi:2000  uvicorn chromadb....  About a minute ago  Up About a minute (healthy)  0.0.0.0:8010->8000/tcp  chromadb-my-a2rchi
8d561db18278  docker.io/library/postgres:16      postgres              About a minute ago  Up About a minute (healthy)  5432/tcp                postgres-my-a2rchi
a1f7f9b44b1d  localhost/chat-my-a2rchi:2000      python -u a2rchi/...  About a minute ago  Up About a minute            0.0.0.0:7868->7868/tcp  chat-my-a2rchi
```

To access the chat interface, visit its corresponding port (`0.0.0.0:7868` in the above example )

### Removing deployment

Lastly, to tear down the deployment, simply run:
```nohighlight
a2rchi delete --name my-a2rchi
```
You can use the `--rmi` option to remove the images,
```nohighlight
a2rchi delete --name my-a2rchi --rmi
```

## Helpful Notes for Production Deployments

You may wish to use the CLI in order to stage production deployments. This section covers some useful notes to keep in mind.

### Running multiple deployments on the same machine

The CLI is built to allow multiple deployments to run on the same daemon in the case of docker (podman has no daemon). The container networks between all the deployments are seperate, so there is very little risk of them accidentally communicating with one another.

However, you need to be careful with the external ports. Suppose you're running two deployments and both of them are running the chat on external port 8000. There is no way to view both deployments at the same time from the same port, so instead you should split to forwarding the deployments to other external ports. Generally, this can be done in the configuration:
```
interfaces:
  chat_app:
    EXTERNAL_PORT: 7862 # default is 7681
  uploader_app:
    EXTERNAL_PORT: 5004 # default is 5003
  grafana:
    EXTERNAL_PORT: 3001 # default is 3000

utils:
  data_manager:
    chromadb_external_port: 8001 # default is 8000
```

### Persisting data between deployments

Volumes persist between deployments, so if you deploy an instance, and upload some further documents, you will not need to redo this every time you deploy. Of course, if you are editing any data, you should explicitly remove this infromation from the volume, or simply remove the volume itself with
```nohighlight
docker/podman volume rm <volume name>
```

You can see what volumes are currently up with
```nohighlight
docker/podman volume ls
```