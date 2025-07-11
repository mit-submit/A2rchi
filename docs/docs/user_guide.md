# User Guide

A2rchi is built with several interfaces which collaborate with a CORE in order to create a customized RAG system. If you haven't already, read out `Getting Started` page to install, create, and run the CORE. 

The user's guide is broken up into detailing the various interfaces and the secrets/configurations needed for those interfaces. 

To include an interface, simply add it's tag at the end of the `create` CLI command. For example, to include the document uploader, run:
```
$ a2rchi create --name my-a2rchi --a2rchi-config example_conf.yaml --document-uploader True
```

## CORE Interface

TODO: add description of interface here

### Secrets

### Optional configuration fields (see required in Getting Started page)

1. **`chains:input_lists`**: A list of file(s), each containing a list of websites separated by new lines, used for A2rchi's starting context (more can be uploaded later). For example, `configs/miscellanea.list` contains information of the MIT Professors who started the A2rchi project:
```
# web pages of various people
https://people.csail.mit.edu/kraska
https://physics.mit.edu/faculty/christoph-paus
```
Then, include the file in the config:
```
chains:
  input_lists:
    - configs/miscellanea.list
```

## Adding Documents and the Uploader Interface

### Adding Documents

There are two main ways to add documents to A2rchi's vector database. They are
·
- Adding lists of online pdf sources to the configuration to be uploaded at start up
- Manually adding files while the service is running via the uploader.

Both methods are outlined below

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
```
--document-uploader True
```
The exact port may vary based on configuration (default is `5001`). A simple `docker ps -a` command run on the server will inform which port it's being run on.

In order to access the manager, one must first make an account. To do this, first get the ID or name of the uploader container using `docker ps -a`. Then, acces the container using
```
docker exec -it <CONTAINER-ID> bash
```
so you can run
```
python bin/service_create_account.py
```
from the `/root/A2rchi/a2rchi` directory.·

This script will guide you through creating an account. Note that we do not garuntee the security of this account, so never upload critical passwords to create it.·

Once you have created an account, visit the outgoing port of the data manager docker service and then log in. The GUI will then allow you to upload documents while a2rchi is still running. Note that it may take a few minutes for all the documents to upload.


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

```
a2rchi create --name my_piazza_service --a2rchi-config configs/my_piazza_config.yaml --podman --piazza True
```

## Cleo/Mailbox Interface

TODO: add description of interface here

### Secrets

### Configuration

## Grafana Interface 

To run the grafana service, you first need to specify a password for the grafana to access the postgres database that stores the information. Simply set the environment variable as follows:
```
export GRAFANA_PG_PASSWORD=<your_password>
```
Once this is set, add the following argument to your a2rchi create command, e.g.,
```
a2rchi create --name gtesting2 --a2rchi-config configs/example_config.yaml --grafana True
```
and you should see something like this
```
CONTAINER ID  IMAGE                                     COMMAND               CREATED        STATUS                  PORTS                             NAMES
d27482864238  localhost/chromadb-gtesting2:2000         uvicorn chromadb....  9 minutes ago  Up 9 minutes (healthy)  0.0.0.0:8000->8000/tcp, 8000/tcp  chromadb-gtesting2
87f1c7289d29  docker.io/library/postgres:16             postgres              9 minutes ago  Up 9 minutes (healthy)  5432/tcp                          postgres-gtesting2
40130e8e23de  docker.io/library/grafana-gtesting2:2000                        9 minutes ago  Up 9 minutes            0.0.0.0:3000->3000/tcp, 3000/tcp  grafana-gtesting2
d6ce8a149439  localhost/chat-gtesting2:2000             python -u a2rchi/...  9 minutes ago  Up 9 minutes            0.0.0.0:7861->7861/tcp            chat-gtesting2
```
where the grafana interface is accessible at `0.0.0.0:3000`. The default login and password are both "admin", which you will be prompted to change should you want to after first logging in. Navigate to the A2rchi dashboard from the home page by going to the menu > Dashboards > A2rchi > A2rchi Usage.
Pro tip: once at the web interface, for the "Recent Conversation Messages (Clean Text + Link)" panel, click the three little dots in the top right hand corner of the panel, click "Edit", and on the right, go to e.g., "Override 4" (should have Fields with name: clean text, also Override 7 for context column) and override property "Cell options > Cell value inspect". This will allow you to expand the text boxes with messages longer than can fit. Make sure you click apply to keep the changes. Pro tip 2: If you want to download all of the information from any panel as a CSV, go to the same three dots and click "Inspect", and you should see the option.

### Secrets

### Configuration

## Grader Interface

Interface to launch a website which for a provided solution and rubric (and a couple of other things detailed below), will grade scanned images of a handwritten solution for the specified problem(s).

Nota bene: this is not yet fully generalized and "service" ready, but instead for testing grading pipelines and a base off of which to build a potential grading app.

### Requirements

To launch the service the following files are required:

- `users.csv`. This file is .csv file that contains two columns: "MIT email" and "Unique code", e.g.:

```
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

```
a2rchi create --name grader --a2rchi-config configs/my_grading_config.yaml --podman --gpu --grader
```