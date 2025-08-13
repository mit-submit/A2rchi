# User Guide

A2rchi is built with several interfaces which supplement a base deployment in order to create a customized RAG system. If you haven't already, read the `Getting Started` page to install and deploy a basic version of A2rchi.

The user's guide is broken up into detailing the additional command line options, the different parameters accessible via the configuration file, and the various interfaces and the secrets/configurations they require.

### Optional command line options

There are a few additional options you can pass to the `create` command that are not specific to a given interface.

1. **`--podman`**: If your machine is running Podman, you should pass this flag. The CLI will otherwise default to using Docker. 

    Note, if using Podman, to ensure your containers stay running for extended periods, you need to enable lingering. To do this, the following command should work:

          loginctl enable-linger

    To check/confirm the lingering status, simply do

          loginctl user-status | grep -m1 Linger

    Click [here](https://access.redhat.com/solutions/7054698) to read more.



2. **`--gpu`**: This will deploy A2rchi onto the GPUs on your machine, which you will need to do should you decide to run open-source models. NOTE: this has only been tested with Podman, so will likely not work with Docker, for now.

    There are a few additional system requirements for this to work:

    First, make sure you have nvidia drivers installed. Then, for the containers where a2rchi will run to access the GPUs, please install the [nvidia container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). Then, for Podman, run

          sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
          
    Then, the following command
          
          nvidia-ctk cdi list
          
    should show an output that includes
          
          INFO[0000] Found 9 CDI devices 
          ...
          nvidia.com/gpu=0
          nvidia.com/gpu=1
          ...
          nvidia.com/gpu=all
          
    These listed "CDI devices" will be referenced to run A2rchi on the GPUs, so make sure this is there. To see more about accessing GPUs with Podman, click [here](https://podman-desktop.io/docs/podman/gpu).
    If you have Docker, run

          sudo nvidia-ctk runtime configure --runtime=docker

    What follows should be the same as above -- NOTE: this has not been tested yet with Docker. To see more about accessing GPUs with Docker, click [here](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuration).

    
    Once these requirements are met, the `--gpu` option will automatically deploy A2rchi across all GPUs on the machine. If you want to instead select specific GPUs see the `--gpu-ids` options below


3. **`--gpu-ids`**: Instead of `--gpu`, you can select one or more specific GPU ids, e.g., in case some are in use. Options are `all` (same as `--gpu`), or integers, e.g., `0` or `0,1` (multiple ids should be separated by commas)

4. **`--tag`**: The tag for the images that are built locally. Can be useful when trying different configurations.

5. **`--jira`**: If True, it will make A2rchi fetch ticket data from the JIRA ticketing system and insert the documents into its vector database. Additional configuration and secret are needed for this option. See below for details.

6. **`--debug`**: Flag to set logging level to DEBUG. Default is INFO.


### Optional configuration fields (see required in Getting Started page)

#### RAG-related options

1. **`chains:input_lists`**: A list of file(s), each containing a list of websites separated by new lines, used for A2rchi's starting context (more can be uploaded later). For example, `configs/miscellanea.list` contains information of the MIT Professors who started the A2rchi project:

        # web pages of various people
        https://people.csail.mit.edu/kraska
        ttps://physics.mit.edu/faculty/christoph-paus
    
    Then, include the file in the config:
    
        chains:
          input_lists:
            - configs/miscellanea.list
    
2. **`utils:data_manager:CHUNK_SIZE`**: Number of characters that define a "chunk", i.e., a string that will get embdedded and stored in the vector database. Default is `1000`.

3. **`utils:data_manager:CHUNK_OVERLAP`**: When splitting documents into chunks, how much should they overlap. Default is `0`.

4. **`utils:data_manager:num_documents_to_retrieve`**: How many chunks to query in order of decreasing similarity (so 1 would return the most similar only, 2 the next most similar, etc.).

5. **`utils:embeddings:EMBEDDING_NAME`**: `OpenAIEmbeddings` (default) or `HuggingFaceEmbeddings`. To choose the specific model, see next lines.

6. **`utils:embeddings:EMBEDDING_CLASS_MAP:OpenAIEmbeddings:kwargs:model_name`**: The OpenAI embedding model you want to use. Default is `text-embedding-3-small`.

7. **`utils:embeddings:EMBEDDING_CLASS_MAP:OpenAIEmbeddings:similarity_score_reference`**: The threshold for whether to include the link to the most relevant context in the chat response. It is an approximate distance (chromadb uses an HNSW index, where default distance function is l2 -- see more [here](https://docs.trychroma.com/docs/collections/configure)), so smaller values represent higher similarity. The link will be included if the score is *below* the chosen value. Default is `10` (scores are usually order 1, so default is to always include link).

8. **`utils:embeddings:EMBEDDING_CLASS_MAP:HuggingFaceEmbeddings:kwargs:model_name`**: The HuggingFace embedding model you want to use. Default is `sentence-transformers/all-MiniLM-L6-v2`. TODO: fix logic to require token if private model is requested.

9. **`utils:embeddings:EMBEDDING_CLASS_MAP:HuggingFaceEmbeddings:kwargs:model_kwargs:device`**: Argument passed to embedding model initialization, to load onto `cpu` (default) or `cuda` (GPU), which you can select if you are deploying a2rchi onto GPU.

10. **`utils:embeddings:EMBEDDING_CLASS_MAP:HuggingFaceEmbeddings:kwargs:encode_kwargs:normalize_embeddings`**: Whether to normalize the embedded vectors or not. Default is `true`. Note, the default distance metric that chromadb uses is l2, which mesasures the absolute geometric distance between vectors, so whether they are normalized or not will affect the search.

11. **`utils:embeddings:EMBEDDING_CLASS_MAP:HuggingFaceEmbeddings:similarity_score_reference`**: Same as #7.


#### Chat Service

Additional configuration options for the chatbot, deployed automatically with A2rchi:

1. **`interfaces:chat_app:PORT`**: Internal port that the Flask application binds to inside the container. This is the port the Flask server listens on within the container's network namespace. Usually don't need to change this unless you have port conflicts within the container. Default is `7861`.

2. **`interfaces:chat_app:EXTERNAL_PORT`**: External port that maps to the container's internal port, making the chat application accessible from outside the container. This is the port users will connect to in their browser (e.g., `your-hostname:7861`). When running multiple deployments on the same machine, each deployment must use a different external port to avoid conflicts. Default is `7861`.

3. **`interfaces:chat_app:HOST`**: Network interface address that the Flask application binds to inside the container. Setting this to `0.0.0.0` allows the application to accept connections from any network interface, which is necessary for the application to be accessible from outside the container. Shouldn't remain unchanged unless you have specific networking requirements. Default is `0.0.0.0`.

4. **`interfaces:chat_app:HOSTNAME`**: The hostname or IP address that client browsers will use to make API requests to the Flask server. This gets embedded into the JavaScript code and determines where the frontend sends its API calls. Must be set to the actual hostname/IP of the machine running the container. Using `localhost` will only work if accessing the application from the same machine. Default is `localhost`.

5. **`interfaces:chat_app:num_responses_until_feedback`**: Number of responses before the user is encouraged to provide feedback.

6. **`interfaces:chat_app:flask_debug_mode`**: Boolean for whether to run the flask app in debug mode or not. Default is True.

#### JIRA

Find below the configuration fields for JIRA feature.

1. **`utils:jira:JIRA_URL`**: The URL of the JIRA instance from which A2rchi will fetch data. Its type is string. This option is required if `--jira` flag is used.
2. **`utils:jira:JIRA_PROJECTS`**: List of JIRA project names that A2rchi will fetch data from. Its type is a list of strings. This option is required if `--jira` flag is used.
3. **`utils:jira:ANONYMIZE_DATA`**: Boolean flag indicating whether the fetched data from JIRA should be anonymized or not. This option is optional if `--jira` flag is used. Its default value is True.

##### JIRA secret

A personal access token (PAT) is required to authenticate and authorize with JIRA. This token should be put in a file called `jira_pat.txt`. This file should be put in the secrets folder.

#### Anonymizer

Find below the configuration fields for anonymization feature. All of them are optional.

1. **`utils:anonymizer:nlp_model`**: The NLP model that the `spacy` library will use to perform Name Entity Recognition (NER). Its type is string. 
2. **`utils:anonymizer:excluded_words`**: The list of words that the anonymizer should remove. Its type is list of strings. 
3. **`utils:anonymizer:greeting_patterns`**: The regex pattern to use match and remove greeting patterns. Its type is string.
4. **`utils:anonymizer:signoff_patterns`**: The regex pattern to use match and remove signoff patterns. Its type is string.
5. **`utils:anonymizer:email_pattern`**: The regex pattern to use match and remove email addresses. Its type is string.
6. **`utils:anonymizer:username_pattern`**: The regex pattern to use match and remove JIRA usernames. Its type is string.


## Adding Documents and the Uploader Interface

### Adding Documents

There are two main ways to add documents to A2rchi's vector database. They are:

- Adding lists of online pdf sources to the configuration to be uploaded at start up
- Manually adding files while the service is running via the uploader.
- Directly copying files into the container, in a pinch

These methods are outlined below

#### Document Lists

Before starting the a2rchi service, one can create a document list, which is a `.list` file containing *links* that point to either `html`, `txt`, or `pdf` files. `.list` files are also able to support comments, using "#". They are also generally stored in the `config` folder of the repository. For example, the below may be a list

```
# Documents for the 6.5830 class
https://dsg.csail.mit.edu/6.5830/index.php
https://db.csail.mit.edu/madden/
https://people.csail.mit.edu/kraska/
https://dsg.csail.mit.edu/6.5830/syllabus.php
https://dsg.csail.mit.edu/6.5830/faq.php
https://dsg.csail.mit.edu/6.5830/lectures/lec1-notes.pdf
https://dsg.csail.mit.edu/6.5830/lectures/lec2-notes.pdf
https://dsg.csail.mit.edu/6.5830/lectures/lec3-notes.pdf
```

Once you have created and saved the list in the repository, simply add it to the configuration of the deployment you would like to run under `chains/input-lists` such as
```
chains:
  input_lists:
    - empty.list
    - submit.list
    - miscellanea.list
```
When you restart the service, all the documents will be uploaded to the vector store. Note, this may take a few minutes.

#### Manual Uploader

In order to upload papers while a2rchi is running via an easily accessible GUI, use the data manager built into the system. The manager is run as an additional docker service by adding the following argument to the CLI command: 
```nohighlight
--document-uploader 
```
The exact port may vary based on configuration (default is `5001`). A simple `docker ps -a` command run on the server will inform which port it's being run on.

In order to access the manager, one must first make an account. To do this, first get the ID or name of the uploader container using `docker ps -a`. Then, acces the container using
```nohighlight
docker exec -it <CONTAINER-ID> bash
```
so you can run
```
python bin/service_create_account.py
```
from the `/root/A2rchi/a2rchi` directory.·

This script will guide you through creating an account. Note that we do not garuntee the security of this account, so never upload critical passwords to create it.·

Once you have created an account, visit the outgoing port of the data manager docker service and then log in. The GUI will then allow you to upload documents while a2rchi is still running. Note that it may take a few minutes for all the documents to upload.

#### For quick alternative

The documents used for RAG live in the chat container at `/root/data/<directory>/<files>`. Thus, in a pinch, you can `docker/podman cp` a file at this directory level, e.g., `podman/docker cp myfile.pdf <container name or ID>:/root/data/<new_dir>/`. If you need to make a new directory in the container, you can do `podman exec -it <container name or ID> mkdir /root/data/<new_dir>`.


## Piazza Interface

Set up A2rchi to read posts from your Piazza forum and post draft responses to a specified slack channel (other options coming soon). To do this, a Piazza login (email and password) is required, plus the network ID of your Piazza channel, and lastly, a Webhook for the slack channel A2rchi will post to. See below for a step-by-step description of this.

1. Go to https://api.slack.com/apps and sign in to workspace where you will eventually want A2rchi to post to (note doing this in MIT workspace will require approval of the app/bot).
2. Click 'Create New App', and then 'From scratch'. Name your app and again select the correct workspace. Then hit 'Create App'
3. Now you have your app, and there are a few things to configure before you can launch A2rchi:
4. Go to Incoming Webhooks under Features, and toggle it on.
5. Click 'Add New Webhook', and select the channel you want A2rchi to post to.
6. Now, copy the 'Webhook URL' and paste it into a file called 'slack_webhook.txt', and handle it like any other secret!

### Secrets

The necessary secrets for deploying the Piazza service are the following:

- `slack_webhook.txt`
- `piazza_email.txt`
- `piazza_password.txt`

The slack webhook secret is described above. The piazza email and password should be those of one of the class instructors. Remember to put this information in files named following what is written above.

### Configuration

Beyond standard required configuration fields, the network ID of the Piazza channel is required (see below for an example config). You can get the network ID by simply navigating to the class homepage, and grabbing the sequence that follows 'https://piazza.com/class/'. For example, the 8.01 Fall 2024 homepage is: 'https://piazza.com/class/m0g3v0ahsqm2lg'. The network ID is thus 'm0g3v0ahsqm2lg'. Example minimal config for the Piazza interface:

```
name: bare_minimum_configuration #REQUIRED

global:
  TRAINED_ON: "Your class materials" #REQUIRED

chains:
  input_lists: #REQUIRED
    - configs/class_info.list # list of websites with class info
  chain:
    MODEL_NAME: OpenAIGPT4 #REQUIRED
    CONDENSE_MODEL: OpenAIGPT4 #REQUIRED
    SUMMARY_MODEL_NAME: OpenAIGPT4 #REQUIRED
  prompts:
    CONDENSING_PROMPT: config_old/prompts/condense.prompt #REQUIRED
    MAIN_PROMPT: config_old/prompts/submit.prompt #REQUIRED
    SUMMARY_PROMPT: config_old/prompts/summary.prompt #REQUIRED

location_of_secrets: #REQUIRED
  - ~/.secrets/a2rchi_base_secrets
  - ~/.secrets/piazza

utils:
  piazza:
    network_id: <your Piazza network ID here> # REQUIRED
```

### Running the Piazza service

To run the Piazza service, simply add the piazza flag. For example:

```nohighlight
a2rchi create --name my_piazza_service --a2rchi-config configs/my_piazza_config.yaml --podman --piazza 
```

## Cleo/Mailbox Interface

TODO: add description of interface here

### Secrets

### Configuration


## Mattermost Interface

Set up A2rchi to read posts from your mattermost forum and post draft responses to a specified mattermost channel.

### Secrets

You need to specify a webhook, a key and the id of two channels to read and write. Should be specified like this.

- `mattermost_webhook.txt`
- `mattermost_pak.txt`
- `mattermost_channel_id_read.txt`
- `mattermost_channel_id_write.txt`

location_of_secrets: #REQUIRED
  - ~/.secrets/mattermost

### Running the Mattermost service

To run the Mattermost service, simply add the mattermost flag. For example:

```nohighlight
a2rchi create --name my_mm_service --a2rchi-config configs/my_mm_config.yaml --podman --mattermost 
```



## Grafana Interface 

To run the grafana service, you first need to specify a password for the grafana to access the postgres database that stores the information. Simply set the environment variable as follows:
```nohighlight
export GRAFANA_PG_PASSWORD=<your_password>
```

Note, if you are deploying a version of A2rchi you have already used (i.e., you haven't removed the images/volumes for a given `--name`), the postgres will have already been created without the grafana user created, and it will not work, so make sure to deploy a fresh instance.

Once this is set, add the following argument to your a2rchi create command, e.g.,
```nohighlight
a2rchi create --name gtesting2 --a2rchi-config configs/example_config.yaml --grafana 
```
and you should see something like this
```
CONTAINER ID  IMAGE                                     COMMAND               CREATED        STATUS                  PORTS                             NAMES
d27482864238  localhost/chromadb-gtesting2:2000         uvicorn chromadb....  9 minutes ago  Up 9 minutes (healthy)  0.0.0.0:8000->8000/tcp, 8000/tcp  chromadb-gtesting2
87f1c7289d29  docker.io/library/postgres:16             postgres              9 minutes ago  Up 9 minutes (healthy)  5432/tcp                          postgres-gtesting2
40130e8e23de  docker.io/library/grafana-gtesting2:2000                        9 minutes ago  Up 9 minutes            0.0.0.0:3000->3000/tcp, 3000/tcp  grafana-gtesting2
d6ce8a149439  localhost/chat-gtesting2:2000             python -u a2rchi/...  9 minutes ago  Up 9 minutes            0.0.0.0:7861->7861/tcp            chat-gtesting2
```
where the grafana interface is accessible at `your-hostname:3000`. To change the external port from `3000`, you can do this in the config at `interfaces:grafana:EXTERNAL_PORT`. The default login and password are both "admin", which you will be prompted to change should you want to after first logging in. Navigate to the A2rchi dashboard from the home page by going to the menu > Dashboards > A2rchi > A2rchi Usage. Note, `your-hostname` here is the just name of the machine. Grafana uses its default configuration which is `localhost` but unlike the chat interface, there are no APIs where we template with a selected hostname, so the container networking handles this nicely.

Pro tip: once at the web interface, for the "Recent Conversation Messages (Clean Text + Link)" panel, click the three little dots in the top right hand corner of the panel, click "Edit", and on the right, go to e.g., "Override 4" (should have Fields with name: clean text, also Override 7 for context column) and override property "Cell options > Cell value inspect". This will allow you to expand the text boxes with messages longer than can fit. Make sure you click apply to keep the changes.

Pro tip 2: If you want to download all of the information from any panel as a CSV, go to the same three dots and click "Inspect", and you should see the option.

### Secrets

### Configuration

## Grader Interface

Interface to launch a website which for a provided solution and rubric (and a couple of other things detailed below), will grade scanned images of a handwritten solution for the specified problem(s).

Nota bene: this is not yet fully generalized and "service" ready, but instead for testing grading pipelines and a base off of which to build a potential grading app.

### Requirements

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

### Secrets

The only grading specific secret is the admin password, which like shown above, should be put in the following file

```
admin_password.txt
```

Then it behaves like any other secret.

### Configuration

The required fields in the configuration file are different from the rest of the A2rchi services. Below is an example:

```
name: grading_test # REQUIRED

global:
  TRAINED_ON: "rubrics, class info, etc." # REQUIRED

locations_of_secrets: # REQUIRED
  - ~/.secrets/api_tokens
  - ~/.secrets/salts_and_internal_passwords
  - ~/.secrets/grader

chains:
  input lists:
    - configs/miscellanea.list
  chain:
    IMAGE_PROCESSING_MODEL_NAME: HuggingFaceImageLLM # REQUIRED
    GRADING_FINAL_GRADE_MODEL_NAME: HuggingFaceOpenLLM # REQUIRED

  prompts:
    IMAGE_PROCESSING_PROMPT: configs/prompts/image_processing.prompt # REQUIRED
    GRADING_FINAL_GRADE_PROMPT: configs/prompts/grading_final_grade.prompt # REQUIRED

interfaces:
  grader_app:
    num_problems: 1 # REQUIRED
    local_rubric_dir: ~/grading/my_rubrics # REQUIRED
    local_users_csv_dir: ~/grading/logins # REQUIRED
```

1. `name` -- The name you give to your configuration.
2. `global.TRAINED_ON` -- A brief description of what you are giving to A2rchi.
3. `chains.input_lists` -- .list files of websites you want A2rchi to be able to RAG. If you don't want to do any RAG, you can pass an empty file.
4. `chains.chain.IMAGE_PROCESSING_MODEL_NAME` -- The class of model you want to use. For now, only `HuggingFaceImageLLM` is supported. The default model used is `Qwen/Qwen2.5-VL-7B-Instruct`. If you want to try others, specify with `chains.chain.MODEL_CLASS_MAP.HuggingFaceImageLLM.kwargs.base_model`. Note -- for now you must remove or comment out `MODEL_NAME` and `CONDENSE_MODEL_NAME`.
5. `chains.chain.GRADING_FINAL_GRADE_MODEL_NAME` -- The class of model you want to use (HuggingFaceOpenLLM, OpenAIGPT4, etc.). The default HuggingFace model is `Qwen/Qwen2.5-7B-Instruct-1M`. If you want to try others, specify with `chains.chain.MODEL_CLASS_MAP.HuggingFaceOpenLLM.kwargs.base_model`.
6. `chains.prompts.IMAGE_PROCESSING_PROMPT` -- A prompt for processing images, which should only be a string (no templating).
7. `chains.prompts.GRADING_FINAL_GRADE_PROMPT` -- A prompt for grading a student's solution.
8. `interfaces.grader_app.num_problems` -- number of problems the grading service should expect (note should match the number of rubric files...)
9. `interfaces.grader_app.local_rubric_dir` -- The directory where the `solution_with_rubric_*.txt` files are.
10. `interfaces.grader_app.local_rubric_dir` -- The directory where the `users.csv` file is.


### Grader-Specific Optional Configuration Fields

1. `chains.chain.GRADING_ANALYSIS_MODEL_NAME` and `chains.prompts.GRADING_ANALYSIS_PROMPT` -- The model name and path to the analysis prompt, respectively, if you want to include the analysis step in the grading chain (recommended to include summary step as well, but not required).
2. `chains.chain.GRADING_SUMMARY_MODEL_NAME` and `chains.prompts.GRADING_SUMMARY_PROMPT` -- The model name and path to the summary prompt, respectively, if you want to include the summary step in the grading chain (this is only used in the analysis step, so you shouldn't include the summary step without it).


### Deployment

With a minimal configuration like that detailed above that is required for the grading service, we are ready to deploy! For example, to launch the grader interface on a machine with gpus (for now, only support open-source models for grader so **--gpu is required**) and podman, the following command will launch a2rchi:

```nohighlight
a2rchi create --name grader --a2rchi-config configs/my_grading_config.yaml --podman --gpu --grader
```