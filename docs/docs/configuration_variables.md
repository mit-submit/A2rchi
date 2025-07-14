# Configuration Variables

This document provides comprehensive documentation for all configuration variables available in A2rchi's base configuration template (`a2rchi/templates/base-config.yaml`).

## Top Level Configuration

### `name`
- **Type**: String
- **Default**: `"default"`
- **Description**: The name identifier for this A2rchi deployment instance.

## Global Configuration (`global`)

### `TRAINED_ON`
- **Type**: String
- **Default**: `""`
- **Description**: Information about what data the system was trained on or configured for.

### `DATA_PATH`
- **Type**: String
- **Default**: `"/root/data/"`
- **Description**: Base path where data files are stored within containers.

### `ACCOUNTS_PATH`
- **Type**: String
- **Default**: `"/root/.accounts/"`
- **Description**: Path where account and authentication-related files are stored.

### `LOCAL_VSTORE_PATH`
- **Type**: String
- **Default**: `"/root/data/vstore/"`
- **Description**: Path where the local vector store data is persisted.

### `ACCEPTED_FILES`
- **Type**: Array of strings
- **Default**: `['.txt', '.html', '.pdf']`
- **Description**: List of file extensions that the system will accept for document upload and processing.

## Interface Configuration (`interfaces`)

### Chat App (`interfaces.chat_app`)

#### `PORT`
- **Type**: Integer
- **Default**: `7861`
- **Description**: Internal port the chat application listens on within the container.

#### `EXTERNAL_PORT`
- **Type**: Integer
- **Default**: `7861`
- **Description**: External port mapped to the chat application for host access.

#### `HOST`
- **Type**: String
- **Default**: `"0.0.0.0"`
- **Description**: Host interface the chat application binds to (0.0.0.0 for all interfaces).

#### `HOSTNAME`
- **Type**: String
- **Default**: `"localhost"`
- **Description**: Hostname used for generating URLs and service discovery.

#### `template_folder`
- **Type**: String
- **Default**: `"/root/A2rchi/a2rchi/interfaces/chat_app/templates"`
- **Description**: Path to HTML templates for the chat interface.

#### `static_folder`
- **Type**: String
- **Default**: `"/root/A2rchi/a2rchi/interfaces/chat_app/static"`
- **Description**: Path to static assets (CSS, JS, images) for the chat interface.

#### `num_responses_until_feedback`
- **Type**: Integer
- **Default**: `3`
- **Description**: Number of responses before prompting user for feedback.

#### `include_copy_button`
- **Type**: Boolean
- **Default**: `false`
- **Description**: Whether to include a copy button for chat responses.

### Uploader App (`interfaces.uploader_app`)

#### `PORT`
- **Type**: Integer
- **Default**: `5001`
- **Description**: Internal port the uploader application listens on.

#### `EXTERNAL_PORT`
- **Type**: Integer
- **Default**: `5003`
- **Description**: External port mapped to the uploader application.

#### `HOST`
- **Type**: String
- **Default**: `"0.0.0.0"`
- **Description**: Host interface for the uploader (0.0.0.0 for public, 127.0.0.1 for internal).

#### `template_folder`
- **Type**: String
- **Default**: `"/root/A2rchi/a2rchi/interfaces/uploader_app/templates"`
- **Description**: Path to HTML templates for the uploader interface.

#### `verify_urls`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Whether to verify URLs before processing them.

### Grader App (`interfaces.grader_app`)

#### `PORT`
- **Type**: Integer
- **Default**: `7861`
- **Description**: Internal port the grader application listens on.

#### `EXTERNAL_PORT`
- **Type**: Integer
- **Default**: `7861`
- **Description**: External port mapped to the grader application.

#### `HOST`
- **Type**: String
- **Default**: `"0.0.0.0"`
- **Description**: Host interface the grader application binds to.

#### `HOSTNAME`
- **Type**: String
- **Default**: `"localhost"`
- **Description**: Hostname for the grader service.

#### `template_folder`
- **Type**: String
- **Default**: `"/root/A2rchi/a2rchi/interfaces/grader_app/templates"`
- **Description**: Path to HTML templates for the grader interface.

#### `num_problems`
- **Type**: Integer
- **Default**: `1`
- **Description**: Number of problems to display or process in the grader.

#### `local_rubric_dir`
- **Type**: String
- **Required**: Yes
- **Description**: Local directory path containing grading rubrics.

#### `local_users_csv_dir`
- **Type**: String
- **Required**: Yes
- **Description**: Local directory path containing user CSV files for grading.

### Grafana (`interfaces.grafana`)

#### `EXTERNAL_PORT`
- **Type**: Integer
- **Default**: `3000`
- **Description**: External port for accessing the Grafana monitoring dashboard.

## Chain Configuration (`chains`)

### Input Lists (`chains.input_lists`)
- **Type**: Array
- **Default**: `[]`
- **Description**: List of input sources or configurations for the chain system.

### Base Chain (`chains.base`)

#### `ROLES`
- **Type**: Array of strings
- **Default**: `['User', 'A2rchi', 'Expert']`
- **Description**: Available roles for conversation participants.

#### `logging.input_output_filename`
- **Type**: String
- **Default**: `"chain_input_output.log"`
- **Description**: Filename for logging chain inputs and outputs.

### Prompts (`chains.prompts`)

#### `CONDENSING_PROMPT`
- **Type**: String
- **Default**: `"/root/A2rchi/condense.prompt"` (if condense model is configured)
- **Description**: Path to the prompt file used for condensing conversation history.

#### `MAIN_PROMPT`
- **Type**: String
- **Default**: `"/root/A2rchi/main.prompt"` (if main model is configured)
- **Description**: Path to the main conversation prompt file.

#### `IMAGE_PROCESSING_PROMPT`
- **Type**: String
- **Default**: `"/root/A2rchi/image_processing.prompt"` (if image model is configured)
- **Description**: Path to the prompt file for image processing tasks.

#### `GRADING_SUMMARY_PROMPT`
- **Type**: String
- **Default**: `"/root/A2rchi/grading_summary.prompt"` (if grading summary model is configured)
- **Description**: Path to the prompt file for generating grading summaries.

#### `GRADING_ANALYSIS_PROMPT`
- **Type**: String
- **Default**: `"/root/A2rchi/grading_analysis.prompt"` (if grading analysis model is configured)
- **Description**: Path to the prompt file for grading analysis.

#### `GRADING_FINAL_GRADE_PROMPT`
- **Type**: String
- **Default**: `"/root/A2rchi/grading_final_grade.prompt"` (if final grade model is configured)
- **Description**: Path to the prompt file for final grade determination.

### Chain Models (`chains.chain`)

#### Model Names

##### `MODEL_NAME`
- **Type**: String
- **Default**: `null`
- **Description**: Primary model identifier for the main conversation chain.

##### `CONDENSE_MODEL_NAME`
- **Type**: String
- **Default**: `null`
- **Description**: Model identifier for condensing conversation history.

##### `IMAGE_PROCESSING_MODEL_NAME`
- **Type**: String
- **Default**: `null`
- **Description**: Model identifier for processing images.

##### `GRADING_SUMMARY_MODEL_NAME`
- **Type**: String
- **Default**: `null`
- **Description**: Model identifier for generating grading summaries.

##### `GRADING_ANALYSIS_MODEL_NAME`
- **Type**: String
- **Default**: `null`
- **Description**: Model identifier for grading analysis.

##### `GRADING_FINAL_GRADE_MODEL_NAME`
- **Type**: String
- **Default**: `null`
- **Description**: Model identifier for final grade determination.

#### `chain_update_time`
- **Type**: Integer
- **Default**: `10`
- **Description**: Time interval (in seconds) for chain updates.

### Model Class Configurations (`chains.chain.MODEL_CLASS_MAP`)

#### AnthropicLLM
- **`model_name`**: String, default `"claude-3-opus-20240229"`
- **`temperature`**: Number, default `1`

#### OpenAIGPT4
- **`model_name`**: String, default `"gpt-4"`
- **`temperature`**: Number, default `1`

#### OpenAIGPT35
- **`model_name`**: String, default `"gpt-3.5-turbo"`
- **`temperature`**: Number, default `1`

#### DumbLLM
- **`sleep_time_mean`**: Number, default `3`
- **`filler`**: String, default `null`

#### LlamaLLM
- **`base_model`**: String, default `"meta-llama/Llama-2-7b-chat-hf"`
- **`peft_model`**: String, default `null`
- **`enable_salesforce_content_safety`**: Boolean, default `false`
- **`quantization`**: Boolean, default `true`
- **`max_new_tokens`**: Integer, default `4096`
- **`seed`**: String, default `null`
- **`do_sample`**: Boolean, default `true`
- **`min_length`**: String, default `null`
- **`use_cache`**: Boolean, default `true`
- **`top_p`**: Number, default `0.9`
- **`temperature`**: Number, default `0.6`
- **`top_k`**: Integer, default `50`
- **`repetition_penalty`**: Number, default `1.0`
- **`length_penalty`**: Number, default `1`
- **`max_padding_length`**: String, default `null`

#### HuggingFaceOpenLLM
- **`base_model`**: String, default `"Qwen/Qwen2.5-7B-Instruct-1M"`
- **`peft_model`**: String, default `null`
- **`enable_salesforce_content_safety`**: Boolean, default `false`
- **`quantization`**: Boolean, default `true`
- **`max_new_tokens`**: Integer, default `4096`
- **`seed`**: String, default `null`
- **`do_sample`**: Boolean, default `true`
- **`min_length`**: String, default `null`
- **`use_cache`**: Boolean, default `true`
- **`top_p`**: Number, default `0.9`
- **`temperature`**: Number, default `0.6`
- **`top_k`**: Integer, default `50`
- **`repetition_penalty`**: Number, default `1.0`
- **`length_penalty`**: Number, default `1`
- **`max_padding_length`**: String, default `null`

#### HuggingFaceImageLLM
- **`base_model`**: String, default `"Qwen/Qwen2.5-VL-7B-Instruct"`
- **`quantization`**: Boolean, default `true`
- **`min_pixels`**: Integer, default `175616`
- **`max_pixels`**: Integer, default `1003520`
- **`max_new_tokens`**: Integer, default `4096`
- **`seed`**: String, default `null`
- **`do_sample`**: Boolean, default `false`
- **`min_length`**: String, default `null`
- **`use_cache`**: Boolean, default `true`
- **`top_k`**: Integer, default `50`
- **`repetition_penalty`**: Number, default `1.0`
- **`length_penalty`**: Number, default `1`

#### VLLM
- **`base_model`**: String, default `"deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"`
- **`seed`**: String, default `null`
- **`max_new_tokens`**: Integer, default `4096`
- **`top_p`**: Number, default `0.9`
- **`temperature`**: Number, default `0.6`
- **`top_k`**: Integer, default `50`
- **`repetition_penalty`**: Number, default `1.0`

## Utility Configuration (`utils`)

### PostgreSQL (`utils.postgres`)

#### `port`
- **Type**: Integer
- **Default**: `5432`
- **Description**: Port for PostgreSQL database connection.

#### `user`
- **Type**: String
- **Default**: `"a2rchi"`
- **Description**: Username for PostgreSQL database authentication.

#### `database`
- **Type**: String
- **Default**: `"a2rchi-db"`
- **Description**: Name of the PostgreSQL database.

#### `host`
- **Type**: String
- **Required**: Yes (set via `postgres_hostname` variable)
- **Description**: Hostname for PostgreSQL database connection.

### Data Manager (`utils.data_manager`)

#### `CHUNK_SIZE`
- **Type**: Integer
- **Default**: `1000`
- **Description**: Size of text chunks for document processing.

#### `CHUNK_OVERLAP`
- **Type**: Integer
- **Default**: `0`
- **Description**: Overlap between consecutive text chunks.

#### `use_HTTP_chromadb_client`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Whether to use HTTP client for ChromaDB connection.

#### `chromadb_host`
- **Type**: String
- **Default**: `"chromadb"`
- **Description**: Hostname for ChromaDB service.

#### `chromadb_port`
- **Type**: Integer
- **Default**: `8000`
- **Description**: Internal port for ChromaDB service.

#### `chromadb_external_port`
- **Type**: Integer
- **Default**: `8000`
- **Description**: External port for ChromaDB service access.

#### `collection_name`
- **Type**: String
- **Required**: Yes (set via `collection_name` variable)
- **Description**: Name of the ChromaDB collection to use.

#### `reset_collection`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Whether to reset the collection on startup.

#### `num_documents_to_retrieve`
- **Type**: Integer
- **Default**: `5`
- **Description**: Number of documents to retrieve for RAG context.

### Embeddings (`utils.embeddings`)

#### `EMBEDDING_NAME`
- **Type**: String
- **Default**: `"OpenAIEmbeddings"`
- **Description**: Name of the embedding model class to use.

#### OpenAI Embeddings (`utils.embeddings.EMBEDDING_CLASS_MAP.OpenAIEmbeddings`)
- **`model`**: String, default `"text-embedding-3-small"`
- **`similarity_score_reference`**: Integer, default `10`

#### HuggingFace Embeddings (`utils.embeddings.EMBEDDING_CLASS_MAP.HuggingFaceEmbeddings`)
- **`model_name`**: String, default `"sentence-transformers/all-MiniLM-L6-v2"`
- **`model_kwargs.device`**: String, default `"cpu"`
- **`encode_kwargs.normalize_embeddings`**: Boolean, default `true`
- **`similarity_score_reference`**: Integer, default `10`

### Scraper (`utils.scraper`)

#### `reset_data`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Whether to reset scraped data on startup.

#### `verify_urls`
- **Type**: Boolean
- **Default**: `false`
- **Description**: Whether to verify URLs before scraping.

#### `enable_warnings`
- **Type**: Boolean
- **Default**: `false`
- **Description**: Whether to enable scraper warnings.

### Piazza Integration (`utils.piazza`)

#### `network_id`
- **Type**: String
- **Required**: Yes
- **Description**: Piazza network ID for integration.

#### `update_time`
- **Type**: Integer
- **Default**: `60`
- **Description**: Update interval in seconds for Piazza data sync.

### Cleo Integration (`utils.cleo`)

#### `cleo_update_time`
- **Type**: Integer
- **Default**: `10`
- **Description**: Update interval in seconds for Cleo integration.

### Mailbox Integration (`utils.mailbox`)

#### `IMAP4_PORT`
- **Type**: Integer
- **Default**: `143`
- **Description**: IMAP4 port for mailbox integration.

#### `mailbox_update_time`
- **Type**: Integer
- **Default**: `10`
- **Description**: Update interval in seconds for mailbox sync.

### JIRA Integration (`utils.jira`)

#### `JIRA_URL`
- **Type**: String
- **Required**: Yes
- **Description**: Base URL for JIRA instance.

#### `JIRA_PROJECTS`
- **Type**: Array of strings
- **Required**: Yes
- **Description**: List of JIRA project keys to integrate.

#### `ANONYMIZE_DATA`
- **Type**: Boolean
- **Default**: `true`
- **Description**: Whether to anonymize JIRA data.

### Anonymizer (`utils.anonymizer`)

#### `nlp_model`
- **Type**: String
- **Default**: `"en_core_web_sm"`
- **Description**: spaCy NLP model for anonymization.

#### `excluded_words`
- **Type**: Array of strings
- **Default**: `['John', 'Jane', 'Doe']`
- **Description**: Words to exclude from anonymization.

#### `greeting_patterns`
- **Type**: Array of strings
- **Default**: `['^(hi|hello|hey|greetings|dear)\\b', '^\\w+,\\s*']`
- **Description**: Regex patterns for detecting greetings.

#### `signoff_patterns`
- **Type**: Array of strings
- **Default**: `['\\b(regards|sincerely|best regards|cheers|thank you)\\b', '^\\s*[-~]+\\s*$']`
- **Description**: Regex patterns for detecting email signoffs.

#### `email_pattern`
- **Type**: String
- **Default**: `"[\\w\\.-]+@[\\w\\.-]+\\.\\w+"`
- **Description**: Regex pattern for detecting email addresses.

#### `username_pattern`
- **Type**: String
- **Default**: `"\\[~[^\\]]+\\]"`
- **Description**: Regex pattern for detecting JIRA usernames.