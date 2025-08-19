# Tutorial Helpers

This directory contains helper scripts and documentation for setting up A2rchi for the LPC session.

## Quick Start Guide

### Prerequisites

Make sure you are on a "heavy node", `ssh <your username>@cmslpc-el9-heavy01.fnal.gov` (`cmslpc-el8-heavy01.fnal.gov` and `cmslpc-el8-heavy02.fnal.gov` also work),  which has a large enough local filesystem to build the containers. See more [here](https://uscms.org/uscms_at_work/physics/computing/getstarted/uaf.shtml#nodes)
For the tutorial session on august 15, we have available 4 additional special nodes:
`cmslpc346.fnal.gov`
`cmslpc347.fnal.gov`
`cmslpc348.fnal.gov`
`cmslpc350.fnal.gov`


### 1. Clone and Setup Python Environment

```bash
# Clone the tutorial branch
git clone -b tutorial https://github.com/mit-submit/A2rchi.git
cd A2rchi

# Create and activate Python virtual environment
python -m venv myenv # on el8 nodes you might have to explictily type /usr/bin/python3.11
source myenv/bin/activate.csh  # for tcsh users
# OR: source myenv/bin/activate  # for bash users

# Install the couple of required packaged to build the a2rchi command
pip install .

# Reset command cache (tcsh only)
rehash

# Verify a2rchi is available
which a2rchi
```

### 2. Run Heavy Node Setup Script

Run the container setup script to configure local storage:

```bash
source tutorial_helpers/setup_heavynode.tcsh
```

This script will:
- Create local scratch storage directories
- Configure podman to use local filesystem
- Set environment variables for container builds
- Reset podman configuration

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

### 4. Edit Configuration for SSH Tunneling

Open `configs/lpc_minimal_config.yaml` and look at:

```yaml
interfaces:
  chat_app:
    EXTERNAL_PORT: 7861
utils:
  chromadb:
    chromadb_external_port: 8000
```

These values listed above are the defaults, however since multiple of you will be running on the same machine, you will need to coordinate and ensure you choose your own port, otherwise the first person will get it and following attempts to use the same will complain.

### 5. Launch A2rchi

Deploy your A2rchi instance:

```bash
a2rchi create --name <deployment-name> --a2rchi-config configs/lpc_minimal_config.yaml --podman
```

Replace `<deployment-name>` with whatever you want to call your deployment (e.g., `my-chatbot`, `test-instance`, etc.), *just make sure it is all lowercase otherwise Podman will complain*!.

**Note**: This might take a bit the first time since we need to pull images, then build some locally, but caching will make it much faster once you start iterating.

### 6. Set Up SSH Port Forwarding

In a **new terminal window** on your local machine (laptop/desktop), create an SSH tunnel:

```bash
ssh -L 7861:localhost:7861 your-username@cmslpc-el9-heavy01.fnal.gov
```

Replace:
- `7861` with the port you configured in step 4
- `your-username` with your actual username

### 7. Access Your Chatbot

Open your web browser and navigate to:

```
http://localhost:7861
```

Again, replace `7861` accordingly. You should now have access to your A2rchi chatbot!

## Troubleshooting

### Common Issues

**Command not found errors:**
- Make sure your virtual environment is activated
- Run `rehash` if using tcsh shell

**Container build failures:**
- Ensure you're on a heavy node with local scratch storage
- Verify the setup script completed successfully
- Check that you have sufficient disk space

**Can't access the web interface:**
- Verify your SSH tunnel is active
- Check that the port numbers match between config and SSH command
- Ensure containers are running: `podman ps`

**API errors:**
- Verify your OpenAI API key is correct and has credits
- Check that the secrets files were created properly


### Checking A2rchi and Container Status

```bash
# See running containers
podman ps

# Check container logs
podman logs <container-name>

# Stop a2rchi system
a2rchi delete --name <deployment-name>
```

## Files in This Directory

- `setup_heavynode.tcsh` - Automated setup script for container storage configuration
- `README.md` - This documentation file


## More links

On the main repo page, you will find links to the User Guide and Getting Started pages, or below some more examples to explore more of what A2rchi is about...
- 🛠️ **[User's Guide](https://mit-submit.github.io/A2rchi/user_guide/)**


## Adding Grafana and Document Uploader Services (Optional)

If you want to include Grafana monitoring and document uploader services, follow these additional steps:

### Clean Up Previous Instance (if needed)
```bash
# Stop existing instance
a2rchi delete --name <deployment-name>

# Remove postgres volume to reinitialize database
podman volume rm a2rchi-pg-<deployment-name>
```

### Configure Additional Service Secrets
```bash
# Set Grafana postgres password (grafana needs a password for its postgres account)
setenv GRAFANA_PG_PASSWORD wassup

# Create uploader service secrets
echo "flaskpw" > ~/.secrets/flask_uploader_app_secret_key.txt
echo "mysalt" > ~/.secrets/uploader_salt.txt
```

### Update Configuration File
Before relaunching, add the following to your `configs/lpc_minimal_config.yaml` and change ports as necessary:

```yaml
interfaces:
  grafana:
    EXTERNAL_PORT: 3000  # default, change as needed
  uploader_app:
    EXTERNAL_PORT: 5003  # default, change as needed
```

### Launch A2rchi with Additional Services
```bash
a2rchi create --name <deployment-name> -f configs/lpc_minimal_config.yaml --podman --grafana --document-uploader
```

### Set Up Uploader User Account
When deployment is complete, create a user account for the document uploader:

```bash
# Enter the uploader container
podman exec -it a2rchi-<deployment-name>_uploader_1 /bin/bash

# Create user account (inside container)
python a2rchi/bin/service_create_account.py

# Type 'STOP' when done creating accounts, then 'exit' to leave container
```

### Set Up Additional SSH Port Forwarding
In **separate terminal windows** on your local machine, create additional SSH tunnels:

```bash
# For Grafana dashboard
ssh -L 3000:localhost:3000 your-username@cmslpc-el9-heavy01.fnal.gov

# For document uploader
ssh -L 5003:localhost:5003 your-username@cmslpc-el9-heavy01.fnal.gov
```

### Access Additional Services
**Grafana Dashboard:**
```
http://localhost:3000
```

**Document Uploader:**
```
http://localhost:5003
```

Replace port numbers with whatever you configured in the yaml file.

Once navigating to the Grafana page, you will be prompted for a username and password: both are `admin`. You will then be prompted to change the password at first login -- this is optional, but if you change it make sure you don't forget! Then navigate to the monitoring page: menu > Dashboards > A2rchi > A2rchi Usage, here you should find the monitoring page. Read more at the [user guide](https://mit-submit.github.io/A2rchi/user_guide/#grafana-interface), in particular for a couple of hints to make the lower table more readable.

For the uploader, once you arrive to web page, log in with the username and password you created before. Here you can upload documents which will be accessible to a2rchi at the next question asked in the chatbot. On this interface, for any documents that you upload, you can just as easily remove them by clicking `Delete` next to the corresponding file.

For any documents that you didn't upload via the uploader but still might want to remove, you can manually enter the container and delete files as follows:

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

## Adding Jira Service (Optional)

If you want to grab JIRA tickets, follow these additional steps:

### Clean Up Previous Instance (if needed)
```bash
# Stop existing instance
a2rchi delete --name <deployment-name>

# Remove postgres volume to reinitialize database
podman volume rm a2rchi-pg-<deployment-name>
```

### Configure Additional Service Secrets
```bash
# Obtain a token:
1. go to https://its.cern.ch/jira/
2. Edit Profile (icon on the top right cornre) and then opeen Personal Access Tokens and click Create token

# Add you jira token in secrets
echo <YOUR TOKEN> > ~/.secrets/jira_pat.txt
```

### Update Configuration File
Before relaunching, add the following to your `configs/lpc_minimal_config.yaml`:

```yaml
utils:
  jira:
    JIRA_URL: https://its.cern.ch/jira/
    JIRA_PROJECTS: ["CMSPROD"]
```

### Launch A2rchi with Additional Services
```bash
a2rchi create --name <deployment-name> -f configs/lpc_minimal_config.yaml --podman --jira
```

#homework 💻 : add one about websites in .list file that you give to input_lists in config and also grabbing JIRA tickets
