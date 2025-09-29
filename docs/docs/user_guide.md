# User Guide

## Overview

A2rchi supports various **data sources** as easy ways to ingest your data into the vector store databased used for document retrieval. These include:

- **Links lists (even behind SSO)**: automatically scrape and ingest documents from a list of URLs
- **Git scraping**: git mkdocs repositories
- **Ticketing systems**: JIRA, Redmine, Piazza
- **Local documents**

Additionally, A2rchi supports various **interfaces/services**, which are applications that interact with the RAG system. These include:

- **Chat interface**: a web-based chat application
- **Piazza integration**: read posts from Piazza and post draft responses to a Slack channel
- **Cleo/Redmine integration**: read emails and create tickets in Redmine
- **Mattermost integration**: read posts from Mattermost and post draft responses to a Mattermost channel
- **Grafana monitoring dashboard**: monitor system and LLM performance metrics
- **Document uploader**: web interface for uploading and managing documents
- **Grader**: automated grading service for assignments with web interface

Both data sources and interfaces/services are enabled via flags to the `a2rchi create` command,
```bash
a2rchi create [...] --services=chatbot,piazza,jira,...
```
The parameters of the services are configured via the configuration file. See below for more details.

We support various **pipelines** which are pre-defined sequences of operations that process user inputs and generate responses.
Each service may support a given pipeline.
See the `Services` and `Pipelines` sections below for more details.
For each pipeline, you can use different models, retrievers, and prompts for different steps of the pipeline.
We support various **models** for both embeddings and LLMs, which can be run locally or accessed via APIs.
See the `Models` section below for more details.
Both pipelines and models are configured via the configuration file.

Finally, we support various **retrievers** and **embedding techniques** for document retrieval.
These are configured via the configuration file.
See the `Vector Store` section below for more details.

---

## Data Sources

These are the different ways to ingest data into the vector store used for document retrieval.

### Web Link Lists

A web link list is a simple text file containing a list of URLs, one per line.
A2rchi will fetch the content from each URL and add it to the vector store, using the `Scraper` class.

#### Configuration

You can define which lists of links A2rchi will inject in the configuration file as follows:
```yaml
data_manager:
  input_lists:  # REQUIRED
    - configs/miscellanea.list  # list of websites with relevant info
    - [...other lists...]
```

Each list should be a simple text file containing one URL per line, e.g.,
```
https://example.com/page1
https://example.com/page2
[...]
```

In the case that some of the links are behind a Single Sign-On (SSO) system, you can use the `SSOScraper`.
To enable it, add the enable it and pick the class you want in the configuration file:
```yaml
utils:
  sso:
    sso_class: CERNSSO  # or whichever class you want to use
    enabled: true
```

#### Secrets

If you are using SSO, depending on the class, you may need to provide your login credentials in a secrets file as follows:
```bash
SSO_USERNAME=username
SSO_PASSWORD=password
```
Then, make sure that the links you provide in the `.list` file(s) start with `sso-`, e.g.,
```
sso-https://example.com/protected/page
```

#### Running

Link scraping is automatically enabled in A2rchi, you don't need to add any arguments to the `create` command.

---

### Git scraping

In some cases, the RAG input may be documentations based on MKDocs git repositories.
Instead of scraping these sites as regular HTML sites you can obtain the relevant content using the `GitScraper` class.

#### Configuration

To configure it, simply add the following field in the configuration file:
```yaml
utils:
  git:
    enabled: {{ utils.git.enabled | default(false, true) }}
```
In the input lists, make sure to prepend `git-` to the URL of the repositories you are interested in scraping.
```nohighlight
git-https://gitlab.cern.ch/cms-tier0-ops/documentation.git
```

#### Secrets

You will need to provide a git username and token in the secrets file,
```bash
GIT_USERNAME=your_username
GIT_TOKEN=your_token
```

#### Running

Git link scraping is automatically enabled in A2rchi once enabled in the config, you don't need to add any arguments to the `create` command.

---

### JIRA

The JIRA integration allows A2rchi to fetch issues and comments from specified JIRA projects and add them to the vector store, using the `JiraScraper` class.

#### Configuration

Select which projects to scrape in the configuration file:
```yaml
jira:
    url: {{ utils.jira.JIRA_URL }}
    projects: 
      {%- for project in utils.jira.JIRA_PROJECTS %}
      - {{ project }}
      {%- endfor %}
    anonymize_data: {{ utils.jira.ANONYMIZE_DATA | default(true, true) }}
```

You can turn on an automatic anonymizer of the data fetched from JIRA via the `anonymize_data` config.
```yaml
  anonymizer:
    nlp_model: {{ utils.anonymizer.nlp_model | default('en_core_web_sm', true) }}
    excluded_words: 
      {%- for word in utils.anonymizer.excluded_words | default(['John', 'Jane', 'Doe']) %}
      - {{ word }}
      {%- endfor %}
    greeting_patterns: 
      {%- for pattern in utils.anonymizer.greeting_patterns | default(['^(hi|hello|hey|greetings|dear)\\b', '^\\w+,\\s*']) %}
      - {{ pattern }}
      {%- endfor %}
    signoff_patterns: 
      {%- for pattern in utils.anonymizer.signoff_patterns | default(['\\b(regards|sincerely|best regards|cheers|thank you)\\b', '^\\s*[-~]+\\s*$']) %}
      - {{ pattern }}
      {%- endfor %}
    email_pattern: '{{ utils.anonymizer.email_pattern | default("[\\w\\.-]+@[\\w\\.-]+\\.\\w+") }}'
    username_pattern: '{{ utils.anonymizer.username_pattern | default("\\[~[^\\]]+\\]") }}'
```

The anonymizer will remove names, emails, usernames, greetings, signoffs, and any other words you specify from the fetched data.
This is useful if you want to avoid having personal information in the vector store.

#### Secrets

A personal access token (PAT) is required to authenticate and authorize with JIRA.
This token should be placed in a secrets file as `JIRA_PAT`.

#### Running

To enable JIRA scraping, run with,
```bash
a2rchi create [...] --services=jira
```

---

### Adding Documents and the Uploader Interface

#### Adding Documents

There are two main ways to add documents to A2rchi's vector database. They are:

- Manually adding files while the service is running via the uploader GUI
- Directly copying files into the container

These methods are outlined below.

#### Manual Uploader

In order to upload documents while A2rchi is running via an easily accessible GUI, use the upload-manager built into the system.
The manager is run as an additional docker service by adding the following argument to the CLI command: 
```bash
a2rchi create [...] --services=uploader 
```
The exact port may vary based on configuration (default is `5001`).
A simple `docker ps -a` command run on the server will inform which port it's being run on.

In order to access the manager, one must first make an account. To do this, first get the ID or name of the uploader container using `docker ps -a`. Then, accese the container using
```nohighlight
docker exec -it <CONTAINER-ID> bash
```
so you can run
```
python bin/service_create_account.py
```
from the `/root/A2rchi/a2rchi` directory.·
This script will guide you through creating an account. Note that we do not guarantee the security of this account, so never upload critical passwords to create it.

Once you have created an account, visit the outgoing port of the data manager docker service and then log in.
The GUI will then allow you to upload documents while A2rchi is still running. Note that it may take a few minutes for all the documents to upload.

#### Directly copying files to the container

The documents used for RAG live in the chat container at `/root/data/<directory>/<files>`. Thus, in a pinch, you can `docker/podman cp` a file at this directory level, e.g., `podman/docker cp myfile.pdf <container name or ID>:/root/data/<new_dir>/`. If you need to make a new directory in the container, you can do `podman exec -it <container name or ID> mkdir /root/data/<new_dir>`.

---

### Redmine

Input from Redmine tickets as a data source.

#### Secrets

```
REDMINE_URL
REDMINE_USER
REDMINE_PW
REDMINE_PROJECT
```

#### Running

```bash
a2rchi create [...] --services=redmine
```

---

## Interfaces/Services

These are the different apps that A2rchi supports, which allow you to interact with the AI pipelines.

### Piazza Interface

Set up A2rchi to read posts from your Piazza forum and post draft responses to a specified Slack channel. To do this, a Piazza login (email and password) is required, plus the network ID of your Piazza channel, and lastly, a Webhook for the slack channel A2rchi will post to. See below for a step-by-step description of this.

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) and sign in to workspace where you will eventually want A2rchi to post to (note doing this in a business workspace like the MIT one will require approval of the app/bot).
2. Click 'Create New App', and then 'From scratch'. Name your app and again select the correct workspace. Then hit 'Create App'
3. Now you have your app, and there are a few things to configure before you can launch A2rchi:
4. Go to Incoming Webhooks under Features, and toggle it on.
5. Click 'Add New Webhook', and select the channel you want A2rchi to post to.
6. Now, copy the 'Webhook URL' and paste it into the secrets file, and handle it like any other secret!

#### Configuration

Beyond standard required configuration fields, the network ID of the Piazza channel is required (see below for an example config). You can get the network ID by simply navigating to the class homepage, and grabbing the sequence that follows 'https://piazza.com/class/'. For example, the 8.01 Fall 2024 homepage is: 'https://piazza.com/class/m0g3v0ahsqm2lg'. The network ID is thus 'm0g3v0ahsqm2lg'.

Example minimal config for the Piazza interface:

```
name: bare_minimum_configuration #REQUIRED

chains:
  input_lists: #REQUIRED
    - configs/class_info.list # list of websites with class info

a2rchi:
  [... a2rchi config ...]

services:
  piazza:
    network_id: <your Piazza network ID here> # REQUIRED
  chat_app:
    trained_on: "Your class materials" #REQUIRED
```

#### Secrets

The necessary secrets for deploying the Piazza service are the following:

```bash
PIAZZA_EMAIL=...
PIAZZA_PASSWORD=...
SLACK_WEBHOOK=...
```

The Slack webhook secret is described above. The Piazza email and password should be those of one of the class instructors. Remember to put this information in files named following what is written above.

#### Running

To run the Piazza service, simply add the piazza flag. For example:

```bash
a2rchi create [...] --services=piazza 
```

---

### Redmine/Mailbox Interface

A2rchi will read all new tickets in a Redmine project, and draft a response as a comment to the ticket.
Once the ticket is updated to the "Resolved" status by an admin, A2rchi will send the response as an email to the user who opened the ticket.
The admin can modify A2rchi's response before sending it out.

#### Configuration

```yaml
redmine: 
  redmine_update_time: {{ utils.redmine.redmine_update_time | default(10, true) }}
  answer_tag: {{ utils.redmine.answer_tag | default('-- A2rchi -- Resolving email was sent', true) }}
```

#### Secrets

```py
required_secrets=['IMAP_USER', 'IMAP_PW', 'REDMINE_URL', 'REDMINE_USER', 
                            'REDMINE_PW', 'REDMINE_PROJECT', 'SENDER_SERVER', 'SENDER_PORT', 
                            'SENDER_REPLYTO', 'SENDER_USER', 'SENDER_PW']
```

#### Running

```bash
a2rchi create [...] --services=redmine-mailer  
```

---

### Mattermost Interface

Set up A2rchi to read posts from your Mattermost forum and post draft responses to a specified Mattermost channel.

#### Configuration

```yaml
mattermost:
  update_time: {{ utils.mattermost.update_time | default(60, true) }}
```

#### Secrets

You need to specify a webhook, a key and the id of two channels to read and write. Should be specified like this.

```bash
MATTERMOST_WEBHOOK=...
MATTERMOST_KEY=...
MATTERMOST_CHANNEL_ID_READ=...
MATTERMOST_CHANNEL_ID_WRITE=...
```

#### Running

To run the Mattermost service, simply add the Mattermost to the services flag. For example:

```bash
a2rchi create [...] --services=mattermost
```

---

### Grafana Interface 

Monitor the performance of your A2rchi instance with the Grafana interface. This service provides a web-based dashboard to visualize various metrics related to system performance, LLM usage, and more.

> Note, if you are deploying a version of A2rchi you have already used (i.e., you haven't removed the images/volumes for a given `--name`), the postgres will have already been created without the Grafana user created, and it will not work, so make sure to deploy a fresh instance.

#### Configuration

```yaml
grafana:
  external_port: {{ services.grafana.external_port | default(3000, true) }}
```

#### Secrets

To run the Grafana service, you first need to specify a password for the Grafana to access the postgres database that stores the information.
Set the environment variable as follows in the secrets file:
```bash
PG_PASSWORD=<your_password>
```

#### Running

Once this is set, add the following argument to your `a2rchi create` command, e.g.,
```bash
a2rchi create [...] --services=grafana
```
and you should see something like this
```nohighlight
CONTAINER ID  IMAGE                                     COMMAND               CREATED        STATUS                  PORTS                             NAMES
d27482864238  localhost/chromadb-gtesting2:2000         uvicorn chromadb....  9 minutes ago  Up 9 minutes (healthy)  0.0.0.0:8000->8000/tcp, 8000/tcp  chromadb-gtesting2
87f1c7289d29  docker.io/library/postgres:16             postgres              9 minutes ago  Up 9 minutes (healthy)  5432/tcp                          postgres-gtesting2
40130e8e23de  docker.io/library/grafana-gtesting2:2000                        9 minutes ago  Up 9 minutes            0.0.0.0:3000->3000/tcp, 3000/tcp  grafana-gtesting2
d6ce8a149439  localhost/chat-gtesting2:2000             python -u a2rchi/...  9 minutes ago  Up 9 minutes            0.0.0.0:7861->7861/tcp            chat-gtesting2
```
where the grafana interface is accessible at `your-hostname:3000`. To change the external port from `3000`, you can do this in the config at `interfaces:grafana:EXTERNAL_PORT`. The default login and password are both "admin", which you will be prompted to change should you want to after first logging in. Navigate to the A2rchi dashboard from the home page by going to the menu > Dashboards > A2rchi > A2rchi Usage. Note, `your-hostname` here is the just name of the machine. Grafana uses its default configuration which is `localhost` but unlike the chat interface, there are no APIs where we template with a selected hostname, so the container networking handles this nicely.

> Pro tip: once at the web interface, for the "Recent Conversation Messages (Clean Text + Link)" panel, click the three little dots in the top right hand corner of the panel, click "Edit", and on the right, go to e.g., "Override 4" (should have Fields with name: clean text, also Override 7 for context column) and override property "Cell options > Cell value inspect". This will allow you to expand the text boxes with messages longer than can fit. Make sure you click apply to keep the changes.

> Pro tip 2: If you want to download all of the information from any panel as a CSV, go to the same three dots and click "Inspect", and you should see the option.

---

### Grader Interface

Interface to launch a website which for a provided solution and rubric (and a couple of other things detailed below), will grade scanned images of a handwritten solution for the specified problem(s).

> Nota bene: this is not yet fully generalized and "service" ready, but instead for testing grading pipelines and a base off of which to build a potential grading app.

#### Requirements

To launch the service the following files are required:

- `users.csv`. This file is .csv file that contains two columns: "MIT email" and "Unique code", e.g.:

```nohighlight
MIT email,Unique code
username@mit.edu,222
```

For now, the system requires the emails to be in the MIT domain, namely, contain "@mit.edu". TODO: make this an argument that is passed (e.g., school/email domain)

- `solution_with_rubric_*.txt`. These are .txt files that contain the problem solution followed by the rubric. The naming of the files should follow exactly, where the `*` is the problem number. There should be one of these files for every problem you want the app to be able to grade. The top of the file should be the problem name with a line of dashes ("-") below, e.g.:

```
Anti-Helmholtz Coils
---------------------------------------------------
```

These files should live in a directory which you will pass to the config, and A2rchi will handle the rest.

- `admin_password.txt`. This file will be passed as a secret and be the admin code to login in to the page where you can reset attempts for students.

#### Secrets

The only grading specific secret is the admin password, which like shown above, should be put in the following file

```bash
ADMIN_PASSWORD=your_password
```

Then it behaves like any other secret.

#### Configuration

The required fields in the configuration file are different from the rest of the A2rchi services. Below is an example:

```
name: grading_test # REQUIRED

a2rchi:
  pipelines:
    - GradingPipeline 
  pipeline_map:
    GradingPipeline:
      prompts:
        required:
          final_grade_prompt: configs/prompts/final_grade.prompt
      models:
        required:
          final_grade_model: OllamaInterface
    ImageProcessingPipeline:
      prompts:
        required:
          image_processing_prompt: configs/prompts/image_processing.prompt
      models:
        required:
          image_processing_model: OllamaInterface

services:
  chat_app:
    trained_on: "rubrics, class info, etc." # REQUIRED
  grader_app:
    num_problems: 1 # REQUIRED
    local_rubric_dir: ~/grading/my_rubrics # REQUIRED
    local_users_csv_dir: ~/grading/logins # REQUIRED

data_manager:
  [...]
```

1. `name` -- The name of your configuration (required).
2. `a2rchi.pipelines` -- List of pipelines to use (e.g., `GradingPipeline`, `ImageProcessingPipeline`).
3. `a2rchi.pipeline_map` -- Mapping of pipelines to their required prompts and models.
4. `a2rchi.pipeline_map.GradingPipeline.prompts.required.final_grade_prompt` -- Path to the grading prompt file for evaluating student solutions.
5. `a2rchi.pipeline_map.GradingPipeline.models.required.final_grade_model` -- Model class for grading (e.g., `OllamaInterface`, `HuggingFaceOpenLLM`).
6. `a2rchi.pipeline_map.ImageProcessingPipeline.prompts.required.image_processing_prompt` -- Path to the prompt file for image processing.
7. `a2rchi.pipeline_map.ImageProcessingPipeline.models.required.image_processing_model` -- Model class for image processing (e.g., `OllamaInterface`, `HuggingFaceImageLLM`).
8. `services.chat_app.trained_on` -- A brief description of the data or materials A2rchi is trained on (required).
9. `services.grader_app.num_problems` -- Number of problems the grading service should expect (must match the number of rubric files).
10. `services.grader_app.local_rubric_dir` -- Directory containing the `solution_with_rubric_*.txt` files.
11. `services.grader_app.local_users_csv_dir` -- Directory containing the `users.csv` file.

#### Running

```bash
a2rchi create [...] --services=grader
```

---

## Models

Models are either:

1. Hosted locally, either via VLLM or HuggingFace transformers.
2. Accessed via an API, e.g., OpenAI, Anthropic, etc.
3. Accessed via an Ollama server instance.

### Local Models

To use a local model, specify one of the local model classes in `models.py`:

- `HuggingFaceOpenLLM`
- `HuggingFaceImageLLM`
- `VLLM`

### Models via APIs

We support the following model classes in `models.py` for models accessed via APIs:

- `OpenAILLM`
- `ClaudeLLM`
- `AnthropicLLM`

### Ollama 

In order to use an Ollama server instance for the chatbot, it is possible to specify `OllamaInterface` for the model name. To then correctly use models on the Ollama server, in the keyword args, specify both the url of the server and the name of a model hosted on the server.  

```
a2rchi:
  chain:
    model_class_map:
      OllamaInterface:
        kwargs:
          base_model: "gemma3" # example 
          url: "url-for-server" 

```

In this case, the `gemma3` model is hosted on the Ollama server at `url-for-server`. You can check which models are hosted on your server by going to `url-for-server/models`.

---

## Other

Some useful additional features supported by the framework.

### Add ChromaDB Document Management API Endpoints

##### Debugging ChromaDB endpoints
Debugging REST API endpoints to the A2rchi chat application for programmatic access to the ChromaDB vector database can be exposed with the following configuration change.
To enable the ChromaDB endpoints, add the following to your config file under `services.chat_app`:

```yaml
services:
  chat_app:
    # ... other config options ...
    enable_debug_chroma_endpoints: true  # Default: false
```

###### ChromaDB  Endpoints Info

####### `/api/list_docs` (GET)
Lists all documents indexed in ChromaDB with pagination support.

**Query Parameters:**
- `page`: Page number (1-based, default: 1)
- `per_page`: Documents per page (default: 50, max: 500)
- `content_length`: Content preview length (default: -1 for full content)

**Response:**
```json
{
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total_documents": 1250,
    "total_pages": 25,
    "has_next": true,
    "has_prev": false
  },
  "documents": [...]
}
```

####### `/api/search_docs` (POST)
Performs semantic search on the document collection using vector similarity.

**Request Body:**
- `query`: Search query string (required)
- `n_results`: Number of results (default: 5, max: 100)
- `content_length`: Max content length (default: -1, max: 5000)
- `include_full_content`: Include complete document content (default: false)

**Response:**
```json
{
  "query": "machine learning",
  "search_params": {...},
  "documents": [
    {
      "content": "Document content...",
      "content_length": 1200,
      "metadata": {...},
      "similarity_score": 0.85
    }
  ]
}
```

---

# Vector Store

TODO: explain vector store, retrievers, and related techniques

### Stemming

By specifying the option stemming within ones configuration, stemming functionality for the documents in A2rchi will be enabled. By doing so, documents inserted into the ragging pipeline, as well as the query that is matched with them, will be stemmed and simplified for faster and more accurate lookup. 

```yaml
utils:
  data_manager:
    stemming:
      ENABLED: true
```
---

# Benchmarking 

A2rchi has benchmarking functionality provided by the `evaluate` CLI command. Before beginning, provide your list of questions in JSON format as follows: 


```json

[

    {
        "question": "",
        "link": "",
        "answer": ""
    },

 ...
     {
        "question": "",
        "link": "",
        "answer": ""
     }
]
```

Then within all of the yaml configuration files that you wish to test, add a configuration for your benchmarking script, which looks like the following:

```yaml
services:
  benchmarking: 
    queries_path: configs/benchmarking/queries.json
    out_dir: bench_out
    modes: 
      - "RAGAS"
      - "LINKS"

```

Finally, before you run the command ensure `out_dir`, the output directory, both exists on your system and that the path is correctly specified so that results can show up inside of it. To run the benchmarking script simply run the following: 

``` bash
a2rchi evaluate -n <name> -e <env_file> -cd <configs_directory> <optionally use  -c <file1>,<file2>, ...> <OPTIONS>
```

Currently, the benchmarking supports both a RAGAS runtime and a LINKS runtime, users can specify which modes they want to run by using the modes section. By default, both are enabled.

The LINKS mode will generate outputs from your A2rchi instance as specified in your other configurations and evaluate it based on if the top k documents retrieved include information from the provided link answer. Note however that this still might mean that the chunks provided as context might still be incorrect, even if they are from the same source link.

The RAGAS mode will use the Ragas RAG evaluator module to return numerical values judging by 4 of their provided metrics: `answer_relevancy`, `faithfulness`, `context precision`, and `context relevancy`. More information about these metrics can be found on their website at: https://docs.ragas.io/en/stable/concepts/metrics/. Note that ragas will by default use OpenAI to evaluate your llm responses and ragging pipeline contexts. To change this, it is possible to specify using other providers such as Anthropic, Ollama, and HuggingFace for your LLM evaluator, as well as HuggingFace for the embeddings. To do so simply specify in the configuration as follows: 

```yaml
services:
  benchmarking: 
    queries_path: configs/benchmarking/queries.json
    out_dir: bench_out
    modes: 
      - "RAGAS"
      - "LINKS"
    mode_settings: 
      ragas_settings: 
        provider: <provider name> # can be one of OpenAI, HuggingFace, Ollama, and Anthropic
        evaluation_model_settings:
          model_name: <model name> # ensure this lines up with the langchain API name for your chosen model and provider
          base_url: <url> # address to your running Ollama server should you have chosen the Ollama provider
        embedding_model: <embedding provider> # OpenAI or HuggingFace
```

You might also want to adjust the `timeout` setting, which is the upper limit on how long the Ragas evaluation takes on a single QA pair, or the `batch_size`, which determines how many QA pairs to evaluate at once, which you might want to adjust, e.g., based on hardware constraints, as Ragas doesn't pay great attention to that. The corresponding configuration options are similarly set for the benchmarking services, as follows:

```yaml
services:
  benchmarking:
    timeout: <time in seconds> # default is 180
    batch_size: <desired batch size> # no default setting, set by Ragas...
```

To later examine your data, there is a folder called plots in the base directory which contains some plotting functions and an ipynotebook with some basic usage examples. This is useful to play around with the results of the benchmarking, we will soon also have instead dedicated scripts to produce the plots of interest.