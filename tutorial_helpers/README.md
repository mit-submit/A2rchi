# A2rchi Deployment Guide for CMS Tutorial (8/25)

This guide walks you through deploying A2rchi on CERN systems (like lxplus) using Podman containers. Follow these steps in order to get your A2rchi instance running.

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

### 2. Check available disk

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
source fun/bin/activate

# Install the a2rchi pacakge
./fun/bin/python -m pip install .
```

### 3. Template Database Initialization

Before deployment, you need to generate the database initialization file:

```bash
# Run the templating script
python template_init_sql.py
```

This will create the file `init.sql`, which we will use to initialize the postgres database that is used to store information from chat interactions, timing info, and more.

### 4. Build Custom PostgreSQL Image

Due to user namespace issues with Podman on lxplus, you need to build a custom PostgreSQL image:

```bash
podman build -f a2rchi/templates/dockerfiles/Dockerfile-postgres -t postgres-custom .
```

Typically, steps 3 and 4 are done automatically in the `create` command we will execute below, but again this was necessary to run on lxplus.

> **Note**: The period (`.`) at the end is important - it sets the build context to the current directory, where `init.sql` lives.

### 5. Deploy A2rchi

Now we will create our A2rchi instance. To do so, we need a configuration file which we will hear more about a bit later. For now, we will use a minimal configuration that you can find at `configs/cms_minimal_configuration.yaml`. It should look like:

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
utils:
  data_manager:
    chromadb_external_port: 8000 # 8000 is the default, same comment for chat port applies here
```

There is no need to change it for now, so we can take this directly and deploy our instance with the following command:

```bash
./fun/bin/a2rchi create --name firsttry -f ~/random_configs/cms_minimal_config.yaml --podman
```

You can replace `firsttry` with your desired instance name.

> **Note**: No capital letters in `--name`, or Podman will complain :)

## Accessing Your Instance

### 6. Port Forwarding

Since we can't directly open ports on CERN login nodes, use SSH port forwarding to access your instance:

```bash
ssh -L 7861:localhost:7861 <your-username>@lxplus<node-number>.cern.ch
```

Then open your browser and navigate to: `http://localhost:7861`. Of course, if you changed the external port for the chat, change from `7861` accordingly. You should now see your chatbot!

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
a2rchi delete --name <deployment name>
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