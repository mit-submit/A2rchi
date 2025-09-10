# Quickstart 

Deploy your first instance of A2rchi and walk through the important concepts.

## Sources and Services

A2rchi can ingest data from a variety of **sources** and supports several **services**. List them with the CLI command, and decide which ones you want to use, so that we can configure them.
```
$ a2rchi list-services
Available A2RCHI services:

Application Services:
  chatbot              Interactive chat interface for users to communicate with the AI agent
  grafana              Monitoring dashboard for system and LLM performance metrics
  uploader             Admin interface for uploading and managing documents
  grader               Automated grading service for assignments with web interface

Integration Services:
  piazza               Integration service for Piazza posts and Slack notifications
  mattermost           Integration service for Mattermost channels
  redmine-mailer       Email processing and Cleo/Redmine ticket management

Data Sources:
  redmine              Redmine issue tracking integration
  jira                 Jira issue tracking integration
```

## Pipelines

A2rchi supports several pipelines, which are pre-defined sequences of operations that process user inputs and generate responses. A particular service will support a subset of the pipelines, see the User's Guide for more details. 

An example pipeline is the `QAPipeline`, which is a question-answering pipeline that takes a user's question, retrieves relevant documents from the vector store, and generates an answer using a language model.

## Configuration

Once you have chosen the services and sources you want to use, you can create a configuration file which specifies these and their settings. You can start from one of the example configuration files in `configs/`, or create your own from scratch.

> **Important:**  
> The configuration file follows the format of `a2rchi/templates/base-config.yaml`, and any fields not specified in your configuration file will be filled in with the defaults from this base config.

Here is an example configuration file which configures some specific settings for the `chatbot` service using the `QAPipeline` pipeline with a local `VLLM` model. Save it as `configs/my_config.yaml`:

```yaml
name: my_a2rchi

global:
  TRAINED_ON: "My data"  

data_manager:
  input_lists:  
    - configs/miscellanea.list
  embedding_name: HuggingFaceEmbeddings
  chromadb_host: localhost

a2rchi:
  pipelines:
    - QAPipeline
  pipeline_map:
    QAPipeline:
      prompts:
        required:
          condense_prompt: configs/prompts/condense.prompt  
          chat_prompt: configs/prompts/submit.prompt  
      models:
        required:
          chat_model: VLLM
          condense_model: VLLM
  model_class_map:
    VLLM:
      kwargs:
        base_model: deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B

interfaces:
  chat_app:
    HOSTNAME: "<your-hostname>" 
```

<details>
<summary>Explanation of config parameters</summary>
Here is a brief explanation of the parameters in the example configuration file:

- `name`: The name of your A2rchi deployment.

- `global:TRAINED_ON`: A brief description of the documents you are uploading to A2rchi.

- `data_manager`: Settings related to data management, including:
  - `input_lists`: A list of files containing links to be ingested.
  - `embedding_name`: The embedding model to use for vectorization.
  - `chromadb_host`: The host where ChromaDB is running.

- `a2rchi`: Settings related to the A2rchi core, including:
  - `pipelines`: The pipelines to use (e.g., `QAPipeline`).
  - `pipeline_map`: Configuration for each pipeline, including prompts and models.
  - `model_class_map`: Mapping of model names to their classes and parameters.

- `interfaces`: Settings for the services/interfaces, including:
  - `chat_app`: Configuration for the chat application, including the hostname.

</details>

## Secrets

Secrets are values which are sensitive and therefore should not be directly included in code or configuration files. They typically include passwords, API keys, etc.

To manage these secrets, we ask that you write them to a location on your file system in  a single `.env` file.

The only secret that is required to launch a minimal version of A2rchi (chatbot with open source LLM and embeddings) is:

- `PG_PASSWORD`: some password you pick which encrypts the database.

So for a basic deployment, all you need is to create the 'secrets' file is:
```bash
echo "PG_PASSWORD=my_strong_password" > ~/.secrets.env
```

If you are not running an open source model, you can use various OpenAI or Anthropic models if you provide the following secrets,

- `openai_api_key`: the API key given by OpenAI
- `anthropic_api_key`: the API key given by Anthropic

respectively. If you want to access private embedding models or LLMs from HuggingFace, you will need to provide the following:

- `hf_token`: the API key to your HuggingFace account

For many of the other services provided by A2rchi, additional secrets are required. These are detailed in User's Guide

## Creating an A2rchi Deployment

Create your deployment with the CLI:
```bash
a2rchi create --name my-a2rchi --config configs/my_config.yaml --podman -e .secrets.env  --services chatbot
```
Here we specify:

- `--name`: the name of your deployment
- `--config`: the path to your configuration file
- `--podman`: use `podman` for container management (`docker` is default)
- `-e`: the path to your secrets
- `--services`: the services to deploy (here we only deploy the `chatbot` service)

<details>
<summary> Output Example</summary>

```bash
$ a2rchi create --name my-a2rchi -c test.yaml --podman -e secrets.env  --services chatbot
```
```
Starting A2RCHI deployment process...
[a2rchi] Creating deployment 'my-a2rchi' with services: chatbot
[a2rchi] Auto-enabling dependencies: postgres, chromadb
[a2rchi] Configuration validated successfully
[a2rchi] You are using an embedding model from HuggingFace; make sure to include a HuggingFace token if required for usage, it won't be explicitly enforced
[a2rchi] Required secrets validated: PG_PASSWORD
[a2rchi] Volume 'a2rchi-pg-my-a2rchi' already exists. No action needed.
[a2rchi] Volume 'a2rchi-my-a2rchi' already exists. No action needed.
[a2rchi] Starting compose deployment from /path/to/my/.a2rchi/a2rchi-my-a2rchi
[a2rchi] Using compose file: /path/to/my/.a2rchi/a2rchi-my-a2rchi/compose.yaml
[a2rchi] (This might take a minute...)
[a2rchi] Deployment started successfully
A2RCHI deployment 'my-a2rchi' created successfully!
Services running: chatbot, postgres, chromadb
[a2rchi] Chatbot: http://localhost:7861
```

</details>

The first time you run this command it will take longer than usual (order minutes) because `docker`/`podman` will have to build the container images from scratch, then subsequent deployments will be quicker.

### Verifying a deployment

Check that a deployment is running with the A2rchi CLI:
```bash
a2rchi list-deployments
```
You should see something like:
```console
Existing deployments:
  my-a2rchi
```

You can also verify that all your images are up and running properly in containers by executing the command:
```bash
podman ps
```
You should see something like:
```console
CONTAINER ID  IMAGE                              COMMAND               CREATED             STATUS                       PORTS                   NAMES
7e823e15e8d8  localhost/chromadb-my-a2rchi:2000  uvicorn chromadb....  About a minute ago  Up About a minute (healthy)  0.0.0.0:8010->8000/tcp  chromadb-my-a2rchi
8d561db18278  docker.io/library/postgres:16      postgres              About a minute ago  Up About a minute (healthy)  5432/tcp                postgres-my-a2rchi
a1f7f9b44b1d  localhost/chat-my-a2rchi:2000      python -u a2rchi/...  About a minute ago  Up About a minute            0.0.0.0:7868->7868/tcp  chat-my-a2rchi
```

To access the chat interface, visit its corresponding port (`0.0.0.0:7868` in the above example).

### Removing a deployment

Lastly, to tear down the deployment, simply run:
```bash
a2rchi delete --name my-a2rchi
```