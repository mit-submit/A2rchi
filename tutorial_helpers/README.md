# A2rchi Deployment Guide for CMS Tutorial (8/25)

This guide walks you through deploying A2rchi on CERN systems (like lxplus) using Podman containers. Follow these steps in order to get your A2rchi instance running.

# Contents
 - [Prerequisites](#prerequisites)
 - [Setup](#setup)             
 - [Accessing Your Instance](#accessing-your-instance)
 - [Troubleshooting](#troubleshooting)
 - [Optional configuration](#optional-setup)
 - [UserGuide links](#more-links)

## Prerequisites

### 1. Check User Namespace Mapping

First, verify you have proper user namespace configuration:

```bash
grep <your-username> /etc/subuid
```

If this command returns no output, you need to request user namespace access:

1. **Join the required egroup**: Go to [this link](https://cern.service-now.com/service-portal?id=kb_article&n=KB0006874) to add yourself to the subordinate-users group. See here for more information on using Podman on lxplus in general: [Podman or Apptainer OCI containers on lxplus](https://cern.service-now.com/service-portal?id=kb_article&n=KB0006874).
2. **Wait for activation**: This may take some time to take effect; up to 24 hours is quoted (for me it took 5 minutes but you never know!)
3. **Verify**: Log out and back in, then test the grep command again

### 2. Login to docker.io

To avoid rate limits for pulling images from docker.io as an anonymous user, login to your docker account from the terminal as follows:

```bash
podman login docker.io
```

This will prompt you for your username and password and automatically set up the configuration file that authenticates you when pulling any images.

### 3. Check available disk

> **Important**: Check the login node you are on has enough disk space to build the containers from scratch: `df -hT /tmp`. There should be at least 55-60G.

For the interested party: NFS mounts behave strangely with rootless contaienrs and won't work, so needs to be local disk. Most login nodes I've seen comfortably satisfy this, but make sure to check. Most of this space is needed for the first build, then after various cleanups what remains is closer to 15-20G... We are working on making available images with all software already included so such space won't be required, but for now it is :)

## Setup

### 2. Clone and Setup Environment

```bash
# Clone the repository
git clone -b cms-tutorial git@github.com:mit-submit/A2rchi.git
cd A2rchi

# Create and activate virtual environment
/usr/bin/python3.11 -m venv fun
source fun/bin/activate # append .csh or .tcsh accordingly if you are running different shell...

# Install the a2rchi pacakge
./fun/bin/python -m pip install .
```

### 3. Configure API Keys and Secrets

Create the secrets directory and add your API keys:

```bash
# Create secrets directory
mkdir -p ~/.secrets

# Add the OpenAI API key (to be provided)
echo "your-openai-api-key-here" > ~/.secrets/openai_api_key.txt

# Set a PostgreSQL password (choose any password you want)
echo "your-chosen-password" > ~/.secrets/pg_password.txt
```

**Note:** The PostgreSQL password can be anything you choose - you won't need to remember it for normal usage.

### 4. Template Database Initialization

Before deployment, you need to generate the database initialization file:

```bash
# Set environment variable to give grafana a password to the database
export GRAFANA_PG_PASSWORD=<insert anything>

# Run the templating script
python tutorial_helpers/initsql-templater.py
```

This will create the file `init.sql`, which we will use to initialize the postgres database that is used to store information from chat interactions, timing info, and more. It also prepares a few files that the grafana service will need.

### 5. Build Custom PostgreSQL and Grafana Images

Due to user namespace issues with Podman on lxplus, you need to build a custom PostgreSQL image:

```bash
podman build -f a2rchi/templates/dockerfiles/Dockerfile-postgres -t postgres-custom .
```

as well as the Grafana image:

```bash
podman build -f a2rchi/templates/dockerfiles/Dockerfile-grafana-custom -t grafana-custom .
```

Typically, steps 4 and 5 are done automatically in the `create` command we will execute below, but again this was necessary to run on lxplus.

> **Note**: The period (`.`) at the end is important - it sets the build context to the current directory, where `init.sql` lives.

### 6. Deploy A2rchi

Now we will create our A2rchi instance. To do so, we need a configuration file which we will hear more about a bit later. For now, we will use a minimal configuration that you can find at `configs/cms_minimal_config.yaml`. It should look like:

```yaml
# bare minimum configuration needed for a2rchi
name: bare_minimum_configuration #REQUIRED

global:
  TRAINED_ON: "Nothing!" #REQUIRED

locations_of_secrets:
  - ~/.secrets/

chains:
  chain:
    MODEL_NAME: OpenAIGPT35
    CONDENSE_MODEL_NAME: OpenAIGPT35
  prompts:
    CONDENSING_PROMPT: configs/prompts/condense.prompt #REQUIRED
    MAIN_PROMPT: configs/prompts/submit.prompt #REQUIRED
interfaces:
  chat_app:
    EXTERNAL_PORT: 7861 # 7861 is the default, you shouldn't have to change this lest someone else is on the same machine already using that port, in which case you will get an error when containers try to run
  grafana:
    EXTERNAL_PORT: 3000  # default, change as needed, same comment for chat port applies here
utils:
  data_manager:
    chromadb_external_port: 8000 # 8000 is the default, same comment for chat port applies here
```

There is no need to change it for now, so we can take this directly and deploy our instance with the following command:

```bash
./fun/bin/a2rchi create --name firsttry -f configs/cms_minimal_config.yaml --podman --grafana
```

You can replace `firsttry` with your desired instance name, but no capital letters in `--name`, or Podman will complain :)

> **Note**: The container building will happen in `/tmp` but all necessary files and templated configurations will be written by default to `~/.a2rchi/a2rchi-<deployment name>/`. They are just a few files so it is lightweight, but if your AFS space is full, you can have this be written to somewhere else by setting the environment variable `A2RCHI_DIR` with the corresponding path.

This will take a while... in particular installing the many packages. When you see the message, `WARNING: Running pip as the 'root' user ...`, it will be there a while.

## Accessing Your Instance

### 7. Port Forwarding the Chatbot

Since we can't directly open ports on CERN login nodes, use SSH port forwarding to access your instance:

```bash
ssh -L 7861:localhost:7861 <your-username>@lxplus<node-number>.cern.ch
```

Then open your browser and navigate to: `http://localhost:7861`. Of course, if you changed the external port for the chat, change from `7861` accordingly. You should now see your chatbot!

### Set Up Additional SSH Port Forwarding
On your local machine, create additional SSH tunnels:

```bash
# For Grafana dashboard
ssh -L 3000:localhost:3000 <your-username>@lxplus<node-number>.cern.ch
```

Again, replace ports as necessary.

### Access Additional Services
**Grafana Dashboard:**
```
http://localhost:3000
```

Replace port numbers with whatever you configured in the yaml file.

Once navigating to the Grafana page, you will be prompted for a username and password: both are `admin`. You will then be prompted to change the password at first login -- this is optional, but if you change it make sure you don't forget! Then navigate to the monitoring page: menu > Dashboards > A2rchi > A2rchi Usage, here you should find the monitoring page. Read more at the [user guide](https://mit-submit.github.io/A2rchi/user_guide/#grafana-interface), in particular for a couple of hints to make the lower table more readable.

For any documents that you might want to remove, you can manually enter the container and delete files as follows:

```bash
# Directly remove a file from the chat container
podman exec -it chat-<deployment-name> rm /root/data/<directory>/<file>
```

Any document in the ChromaDB vector database will exist at this depth. Note, `/root/data` is mounted as a volume called `a2rchi-<deployment>`, so everything in here persists between deployments. Remember, if you want to look inside the container and investigate the structure a bit, you can do so with:

```bash
# Enter the container and open a shell
podman exec -it chat-<deployment-name> /bin/bash
```

and navigate around the container as you please.

## Troubleshooting

### Useful Commands

```bash
# Check running containers
podman ps

# View container logs
podman logs <container-name> # add -f option to follow live

# Should you want to interact directly with the container
podman exec -it <container name> /bin/bash # to open a shell, but you can directly type a command if you want like ls /path/to/dir or something in place of /bin/bash

# Stop the A2rchi instance
./fun/bin/a2rchi delete --name <deployment name>
```

### Common Issues

**Permission Denied Errors**
- Ensure you have user namespace mapping (step 1)
- Try logging out and back in after joining the egroup

**Command not found errors:**
- Make sure your virtual environment is activated
- Make sure you are writing out paths to `python` or `a2rchi` commands since between AFS and virtual environments, something doesn't behave nicely

**Container build failures:**
- Ensure the login node has enough disk space -- there should be at least 55-60G in `/tmp`. NFS mounts behave strangely with rootless contaienrs and won't work, so needs to be local disk. Most login nodes I've seen are comfortably above this but you never know. Note, most of this space is needed for the first build, then there is enough cleanup that what remains is closer to 15-20G...

**Can't access the web interface:**
- Verify your SSH tunnel is active
- Check that the port numbers match between config and SSH command
- Ensure containers are running: `podman ps`

**API errors:**
- Verify your OpenAI API key is correct and has credits
- Check that the secrets files were created properly


## Adding documents

Add some website to `.list` file, like for example `configs/submit.list` which contains the user guide for SubMIT. Pass this file to the config:

```yaml
chains:
  inputs_lists:
    - configs/<your .list filename>.list
```

Then ask A2rchi questions you think it should know the answer to based on the documentation you've given it. Look at the grafana to see what is getting rerieved, if it correct or not, etc.

If not, you might want to try to play with some of the parameters you saw in the slides... or a new technique might need to be implemented!


### Homework 💻

- add one about websites in .list file that you give to input_lists in config and also grabbing JIRA tickets


## More links

On the main repo page, you will find links to the User Guide and Getting Started pages, or below some more examples to explore more of what A2rchi is about...
- 🛠️ **[User's Guide](https://mit-submit.github.io/A2rchi/user_guide/)**