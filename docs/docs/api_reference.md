# API Reference

## CLI

The A2rchi CLI provides commands to create, manage, and delete A2rchi deployments and services.

---

### Commands

#### 1. `create`

Create a new A2rchi deployment.

**Usage:**
```sh
a2rchi create --name <deployment_name> --config <config.yaml> --env-file <secrets.env> [OPTIONS]
```

**Options:**
- `--name, -n` (str, required): Name of the deployment.
- `--config, -c` (str, required): Path to the YAML configuration file.
- `--env-file, -e` (str, required): Path to the secrets `.env` file.
- `--services, -s` (comma-separated): List of services to enable (e.g., `chat_app,uploader_app`).
- `--sources, -src` (comma-separated): Data sources to enable (e.g., `jira,redmine`).
- `--podman, -p`: Use Podman instead of Docker.
- `--gpu-ids`: GPU configuration (`all` or comma-separated IDs).
- `--tag, -t` (str): Image tag for built containers (default: `2000`).
- `--hostmode`: Use host network mode.
- `--verbosity, -v` (int): Logging verbosity (0-4, default: 3).
- `--force, -f`: Overwrite existing deployment if it exists.
- `--dry, --dry-run`: Validate and show what would be created, but do not deploy.

---

#### 2. `delete`

Delete an existing A2rchi deployment.

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

#### 3. `list_services`

List all available A2rchi services and data sources.

**Usage:**
```sh
a2rchi list_services
```

---

#### 4. `list_deployments`

List all existing A2rchi deployments.

**Usage:**
```sh
a2rchi list_deployments
```

---

### Examples

- **Create a deployment:**
  ```sh
  a2rchi create --name mybot --config configs/my.yaml --env-file secrets.env --services chat_app,uploader_app
  ```

- **Delete a deployment and remove images/volumes:**
  ```sh
  a2rchi delete --name mybot --rmi --rmv
  ```

- **List all deployments:**
  ```sh
  a2rchi list_deployments
  ```

- **List all services:**
  ```sh
  a2rchi list_services
  ```

---


## Configuration YAML API Reference

The A2rchi configuration YAML file defines the deployment, services, data sources, pipelines, models, and interface settings for your A2rchi instance.

---

### Top-Level Fields

#### `name`
- **Type:** string
- **Description:** Name of the deployment.

#### `global`
- **TRAINED_ON:** string  
  Description of the data or corpus the system was trained on.
- **DATA_PATH:** string  
  Path to data storage.
- **ACCOUNTS_PATH:** string  
  Path to user accounts.
- **ACCEPTED_FILES:** list  
  Allowed file extensions for uploads.
- **ROLES:** list  
  User roles available in the system.
- **LOGGING.input_output_filename:** string  
  Log file for input/output.
- **verbosity:** int  
  Logging verbosity (0-4).

---

### `interfaces`

Settings for each web interface or service.

#### `chat_app`, `uploader_app`, `grader_app`, `grafana`
- **port:** int  
  Internal port that the Flask application binds to inside the container. This is the port the Flask server listens on within the container's network namespace. Usually don't need to change this unless you have port conflicts within the container. Default is `7861`.
- **external_port:** int  
  External port that maps to the container's internal port, making the chat application accessible from outside the container. This is the port users will connect to in their browser (e.g., `your-hostname:7861`). When running multiple deployments on the same machine, each deployment must use a different external port to avoid conflicts. Default is `7861`.
- **host:** string  
  Network interface address that the Flask application binds to inside the container. Setting this to `0.0.0.0` allows the application to accept connections from any network interface, which is necessary for the application to be accessible from outside the container. Shouldn't remain unchanged unless you have specific networking requirements. Default is `0.0.0.0`.
- **hostname:** string  
  The hostname or IP address that client browsers will use to make API requests to the Flask server. This gets embedded into the JavaScript code and determines where the frontend sends its API calls. Must be set to the actual hostname/IP of the machine running the container. Using `localhost` will only work if accessing the application from the same machine. Default is `localhost`.
- **template_folder:** string  
  Path to HTML templates.
- **static_folder:** string  
  Path to static files (if applicable).
- **num_responses_until_feedback:** int  
  Number of responses before the user is encouraged to provide feedback.
- **include_copy_button:** bool  
  Show copy-to-clipboard button.
- **enable_debug_chroma_endpoints:** bool  
  Enable debug endpoints (chat_app).
- **flask_debug_mode:** bool  
  Enable Flask debug mode.
- **num_problems:** int  
  Number of problems (grader_app).
- **local_rubric_dir:** string  
  Path to rubric files (grader_app).
- **local_users_csv_dir:** string  
  Path to users CSV (grader_app).
- **verify_urls:** bool  
  Verify URLs on upload (uploader_app).

---

### `data_manager`

Controls vector store, chunking, and embedding settings.

- **collection_name:** string  
  Name of the vector collection.
- **input_lists:** list  
  List of files with initial context URLs.
- **local_vstore_path:** string  
  Path to local vector store.
- **embedding_name:** string  
  Embedding backend (`OpenAIEmbeddings`, `HuggingFaceEmbeddings`).
- **embedding_class_map:** dict  
  Embedding backend configuration (see below).
- **chunk_size:** int  
  Number of characters per chunk, i.e., a string that will get embedded and stored in the vector database. Default is `1000`.
- **chunk_overlap:** int  
  When splitting documents into chunks, how much should they overlap. Default is `0`.
- **use_HTTP_chromadb_client:** bool  
  Use HTTP client for ChromaDB.
- **chromadb_host:** string  
  Hostname for ChromaDB.
- **chromadb_port:** int  
  Internal port for ChromaDB.
- **chromadb_external_port:** int  
  Host port for ChromaDB.
- **reset_collection:** bool  
  Reset vector collection on startup.
- **num_documents_to_retrieve:** int  
  How many chunks to query in order of decreasing similarity (so 1 would return the most similar only, 2 the next most similar, etc.).
- **stemming.enabled:** bool  
  Enable stemming for search.
- **distance_metric:** string  
  Distance metric to use for similarity search in ChromaDB. Options are `cosine`, `l2`, and `ip`. Read more (here)[https://docs.trychroma.com/docs/collections/configure]. Default for A2rchi is cosine.
- **use_hybrid_search:** bool  
  Enables hybrid search, that is performing lexical search as well as semantic search. Docs retrieved from both searches are combined. The default is `False`
- **bm25_weight:** float  
  Weight for BM25 in hybrid search.
- **semantic_weight:** float  
  Weight for semantic search in hybrid search.
- **bm25.k1:** float  
  BM25 term frequency saturation. Controls how much the score increases with additional occurrences of a term in a document. Range: `[1.2,2.0]`
- **bm25.b:** float  
  BM25 length normalization. Controls how much the document length influences the score. BM25 normalizes term frequency by document length compared to the average document length in the corpus. Range: `[0,1]`

#### `embedding_class_map`
- **OpenAIEmbeddings:**  
  - **class:** string  
  - **kwargs.model:** string  
  - **similarity_score_reference:** float  
- **HuggingFaceEmbeddings:**  
  - **class:** string  
  - **kwargs.model_name:** string  
    The HuggingFace embedding model you want to use. Default is `sentence-transformers/all-MiniLM-L6-v2`. TODO: fix logic to require token if private model is requested.
  - **kwargs.model_kwargs.device:** string (`cpu` or `cuda`)
    Argument passed to embedding model initialization, to load onto `cpu` (default) or `cuda` (GPU), which you can select if you are deploying a2rchi onto GPU.
  - **kwargs.encode_kwargs.normalize_embeddings:** bool  
    Whether to normalize the embedded vectors or not. Default is `true`. Note, the default distance metric that chromadb uses is l2, which mesasures the absolute geometric distance between vectors, so whether they are normalized or not will affect the search.
  - **similarity_score_reference:** float
    The threshold for whether to include the link to the most relevant context in the chat response. It is an approximate distance (chromadb uses an HNSW index, where default distance function is l2 -- see more [here](https://docs.trychroma.com/docs/collections/configure)), so smaller values represent higher similarity. The link will be included if the score is *below* the chosen value. Default is `10` (scores are usually order 1, so default is to always include link).
  - **query_embedding_instructions:** string or null
    Instructions to accompany the embedding of the query and subsequent document search. Only certain embedding models support this -- see `INSTRUCTION_AWARE_MODELS` in `a2rchi/chains/retrievers.py` to add models that support this. For example, the `Qwen/Qwen3-Embedding-XB` embedding models support this and are listed, see more [here](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B). Default is `None`. You should write the string directly into the config. An example instruction might look like: `"Given a query, retrieve relevant information to answer the query"`. You might tune it to be more specific to your use case which might improve performance.

---

### `a2rchi`

Pipeline and model configuration.

- **pipelines:** list  
  List of enabled pipelines (e.g., `QAPipeline`, `GradingPipeline`).
- **pipeline_map:** dict  
  Configuration for each pipeline:
  - **max_tokens:** int  
  - **prompts.required:** dict  
    Required prompt files for the pipeline.
  - **prompts.optional:** dict  
    Optional prompt files.
  - **models.required:** dict  
    Required models for the pipeline.
  - **models.optional:** dict  
    Optional models.
- **model_class_map:** dict  
  Model backend configuration (see below).
- **chain_update_time:** int  
  Time (seconds) between chain updates.

#### `model_class_map`
Each model (e.g., `AnthropicLLM`, `OpenAIGPT4`, `LlamaLLM`, etc.) has:
- **class:** string  
- **kwargs:** dict  
  Model-specific parameters (see template for details).

---

### `utils`

Utility and integration settings.

- **postgres:**  
  - **port:** int  
  - **user:** string  
  - **database:** string  
  - **host:** string  
- **sso:**  
  - **enabled:** bool  
  - **sso_class:** string  
  - **sso_class_map:** dict  
    - **class:** string  
    - **kwargs:** dict  
- **git:**  
  - **enabled:** bool  
- **scraper:**  
  - **reset_data:** bool  
  - **verify_urls:** bool  
  - **enable_warnings:** bool  
- **piazza:**  
  - **network_id:** string  
  - **update_time:** int  
- **mattermost:**  
  - **update_time:** int  
- **redmine:**  
  - **redmine_update_time:** int  
  - **answer_tag:** string  
- **mailbox:**  
  - **imap4_port:** int  
  - **mailbox_update_time:** int  
- **jira:**  
  - **url:** string  
    The URL of the JIRA instance from which A2rchi will fetch data. Its type is string. This option is required if `--jira` flag is used.
  - **projects:** list  
    List of JIRA project names that A2rchi will fetch data from. Its type is a list of strings. This option is required if `--jira` flag is used.
  - **anonymize_data:** bool  
    Boolean flag indicating whether the fetched data from JIRA should be anonymized or not. This option is optional if `--jira` flag is used. Its default value is True.
- **anonymizer:**  
  - **nlp_model:** string  
    The NLP model that the `spacy` library will use to perform Name Entity Recognition (NER). Its type is string. 
  - **excluded_words:** list  
    The list of words that the anonymizer should remove. Its type is list of strings. 
  - **greeting_patterns:** list  
    The regex pattern to use match and remove greeting patterns. Its type is string.
  - **signoff_patterns:** list  
    The regex pattern to use match and remove signoff patterns. Its type is string.
  - **email_pattern:** string  
    The regex pattern to use match and remove email addresses. Its type is string.
  - **username_pattern:** string  
    The regex pattern to use match and remove JIRA usernames. Its type is string.
---

### Required Fields

Some fields are required depending on enabled services and pipelines.  
For example:
- `name`
- `global.TRAINED_ON`
- `a2rchi.pipelines`
- Service-specific fields (e.g., `utils.piazza.network_id`, `interfaces.grader_app.num_problems`)

See the [User Guide](user_guide.md) for more configuration examples and explanations.

---

### Example

```yaml
name: my_deployment
global:
  TRAINED_ON: "MIT course data"
  DATA_PATH: "/root/data/"
  ACCOUNTS_PATH: "/root/.accounts/"
  ACCEPTED_FILES: [".txt", ".pdf"]
  ROLES: ["User", "A2rchi", "Expert"]
  LOGGING:
    input_output_filename: "chain_input_output.log"
  verbosity: 3

interfaces:
  chat_app:
    port: 7861
    external_port: 7861
    host: "0.0.0.0"
    hostname: "localhost"
    num_responses_until_feedback: 3
    flask_debug_mode: true

data_manager:
  collection_name: "default_collection"
  input_lists: ["configs/miscellanea.list"]
  embedding_name: "OpenAIEmbeddings"
  chunk_size: 1000
  chunk_overlap: 0
  distance_metric: "cosine"
  num_documents_to_retrieve: 5

a2rchi:
  pipelines: ["QAPipeline"]
  pipeline_map:
    QAPipeline:
      max_tokens: 10000
      prompts:
        required:
          condense_prompt: "condense.prompt"
          chat_prompt: "chat.prompt"
      models:
        required:
          condense_model: "DumbLLM"
          chat_model: "DumbLLM"
  model_class_map:
    DumbLLM:
      class: DumbLLM
      kwargs:
        sleep_time_mean: 3
        filler: null

utils:
  postgres:
    port: 5432
    user: "a2rchi"
    database: "a2rchi-db"
    host: "postgres"
```

---

**Tip:**  
For a full template, see `a2rchi/cli/templates/base-config.yaml` in