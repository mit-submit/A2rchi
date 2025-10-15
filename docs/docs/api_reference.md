# API Reference

## CLI

The A2RCHI CLI provides commands to create, manage, and delete A2RCHI deployments and services.

---

### Commands

#### 1. `create`

Create a new A2RCHI deployment.

**Usage:**
```sh
a2rchi create --name <deployment_name> --config <config.yaml> --env-file <secrets.env> [OPTIONS]
```

**Options:**

- `--name, -n` (str, required): Name of the deployment.
- `--config, -c` (str): Path to a YAML configuration file (repeat the flag to supply multiple files).
- `--config-dir, -cd` (str): Directory containing configuration files.
- `--env-file, -e` (str, required): Path to the secrets `.env` file.
- `--services, -s` (comma-separated, required): List of services to enable (e.g., `chatbot,uploader`).
- `--sources, -src` (comma-separated): Additional data sources to enable (e.g., `git,jira`). The `links` source is always available.
- `--podman, -p`: Use Podman instead of Docker.
- `--gpu-ids`: GPU configuration (`all` or comma-separated IDs).
- `--tag, -t` (str): Image tag for built containers (default: `2000`).
- `--hostmode`: Use host network mode.
- `--verbosity, -v` (int): Logging verbosity (0-4, default: 3).
- `--force, -f`: Overwrite existing deployment if it exists.
- `--dry, --dry-run`: Validate and show what would be created, but do not deploy.

---

#### 2. `delete`

Delete an existing A2RCHI deployment.

**Usage:**
```sh
a2rchi delete --name <deployment_name> [OPTIONS]
```

**Options:**

- `--name, -n` (str): Name of the deployment to delete.
- `--rmi`: Remove container images.
- `--rmv`: Remove volumes.
- `--keep-files`: Keep deployment files (do not remove directory).
- `--list`: List all available deployments.

---

#### 3. `list-services`

List all available A2RCHI services and data sources.

**Usage:**
```sh
a2rchi list-services
```

---

#### 4. `list-deployments`

List all existing A2RCHI deployments.

**Usage:**
```sh
a2rchi list-deployments
```

---

#### 5. `evaluate`

Launch the benchmarking runtime to evaluate one or more configurations against a set of questions/answers.

**Usage:**
```sh
a2rchi evaluate --name <run_name> --env-file <secrets.env> --config <file.yaml> [OPTIONS]
```
Use `--config-dir` if you want to point to a directory of configs instead.

**Options:**

- Supports the same flags as `create` (`--sources`, `--podman`, `--gpu-ids`, `--tag`, `--hostmode`, `--verbosity`, `--force`).
- Reads configuration from one or more YAML files that should define the `services.benchmarking` section.

---

### Examples

**Create a deployment:**
```sh
a2rchi create --name mybot --config my.yaml --env-file secrets.env --services chatbot,uploader
```

**Delete a deployment and remove images/volumes:**
```sh
a2rchi delete --name mybot --rmi --rmv
```

**List all deployments:**
```sh
a2rchi list-deployments
```

**List all services:**
```sh
a2rchi list-services
```

---

## Configuration YAML API Reference

The A2RCHI configuration YAML file defines the deployment, services, data sources, pipelines, models, and interface settings for your A2RCHI instance.

---

### Top-Level Fields

#### `name`

- **Type:** string
- **Description:** Name of the deployment.

#### `global`

- **DATA_PATH:** path for persisted data (defaults to `/root/data/`).
- **ACCOUNTS_PATH:** path for uploader/grader account data.
- **ACCEPTED_FILES:** list of extensions allowed for manual uploads.
- **LOGGING.input_output_filename:** log file that stores pipeline inputs/outputs.
- **verbosity:** default logging level for services (0-4).

---

### `services`

Holds configuration for every containerised service. Common keys include:

- **port / external_port:** internal versus host port mapping for web apps.
- **host / hostname:** network binding and public hostname for frontends.
- **volume/paths:** template or static asset paths expected by the service.

Key services:

- **chat_app:** Chat interface options (`trained_on`, ports, UI toggles).
- **uploader_app:** Document uploader settings (`verify_urls`, ports).
- **grader_app:** Grader-specific knobs (`num_problems`, rubric paths).
- **grafana:** Port configuration for the monitoring dashboard.
- **chromadb:** Connection details for the vector store container (`chromadb_host`, `chromadb_port`, `chromadb_external_port`).
- **postgres:** Database credentials (`user`, `database`, `port`, `host`).
- **piazza**, **mattermost**, **redmine_mailbox**, **benchmarking**, ...: Service-specific options (see user guide sections above).

---

### `data_manager`

Controls ingestion sources and vector store behaviour.

- **sources.links.input_lists:** `.list` files with seed URLs.
- **sources.links.scraper:** Behaviour toggles for HTTP scraping (resetting data, URL verification, warning output).
- **sources.<name>.visible:** Mark whether documents harvested from a source should appear in chat citations and other user-facing listings (`true` by default).
- **sources.git.enabled / sources.sso.enabled / sources.jira.enabled / sources.redmine.enabled:** Toggle additional collectors when paired with `--sources`.
- **embedding_name:** Embedding backend (`OpenAIEmbeddings`, `HuggingFaceEmbeddings`, ...).
- **embedding_class_map:** Backend specific parameters (model name, device, similarity threshold).
- **chunk_size / chunk_overlap:** Text splitter parameters.
- **reset_collection:** Whether to wipe the collection before re-populating.
- **num_documents_to_retrieve:** Top-k documents returned at query time.
- **distance_metric / use_hybrid_search / bm25_weight / semantic_weight / bm25.{k1,b}:** Retrieval tuning knobs.
- **utils.anonymizer** (legacy) / **data_manager.utils.anonymizer**: Redaction settings applied when ticket collectors anonymise content.

---

### `a2rchi`

Defines pipelines and model routing.

- **pipelines:** List of pipeline names to load (e.g., `QAPipeline`).
- **pipeline_map:** Per-pipeline configuration of prompts, models, and token limits.
- **model_class_map:** Definitions for each model family (base model names, provider-specific kwargs).
- **chain_update_time:** Polling interval for hot-reloading chains.

---

### `utils`

Utility configuration for supporting components (mostly legacy fallbacks):

- **sso:** Global SSO defaults used when a source-specific override is not provided.
- **git:** Legacy toggle for Git scraping.
- **jira / redmine:** Compatibility settings for ticket integrations; prefer configuring these under `data_manager.sources`.

---

### Required Fields

Some fields are required depending on enabled services and pipelines. For example:

- `name`
- `data_manager.sources.links.input_lists` (or other source-specific configuration)
- `a2rchi.pipelines` and matching `a2rchi.pipeline_map` entries
- Service-specific fields (e.g., `services.piazza.network_id`, `services.grader_app.num_problems`)

See the [User Guide](user_guide.md) for more configuration examples and explanations.

---

### Example

```yaml
name: my_deployment
global:
  DATA_PATH: "/root/data/"
  ACCOUNTS_PATH: "/root/.accounts/"
  ACCEPTED_FILES: [".txt", ".pdf"]
  LOGGING:
    input_output_filename: "chain_input_output.log"
  verbosity: 3

data_manager:
  sources:
    links:
      input_lists:
        - examples/deployments/basic-gpu/miscellanea.list
      scraper:
        reset_data: true
        verify_urls: false
        enable_warnings: false
  utils:
    anonymizer:
      nlp_model: en_core_web_sm
  embedding_name: "OpenAIEmbeddings"
  chunk_size: 1000
  chunk_overlap: 0
  num_documents_to_retrieve: 5

a2rchi:
  pipelines: ["QAPipeline"]
  pipeline_map:
    QAPipeline:
      max_tokens: 10000
      prompts:
        required:
          condense_prompt: "examples/deployments/basic-gpu/condense.prompt"
          chat_prompt: "examples/deployments/basic-gpu/qa.prompt"
      models:
        required:
          condense_model: "OpenAIGPT4"
          chat_model: "OpenAIGPT4"
  model_class_map:
    OpenAIGPT4:
      class: OpenAIGPT4
      kwargs:
        model_name: gpt-4

services:
  chat_app:
    trained_on: "Course documentation"
    hostname: "example.mit.edu"
  chromadb:
    chromadb_host: "chromadb"
```

---

**Tip:**
For a full template, see `src/cli/templates/base-config.yaml` in
the repository.
