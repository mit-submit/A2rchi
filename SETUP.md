## Getting Started with A2rchi
### System Requirements
These instructions were validated on an AWS EC2 c5d.large (2 vCPU, 4GB memory, 20 GiB NVMe SSD) running an Ubuntu 22.04 AMI.

### Install Docker and Docker-Compose
First, if your system does not already have `docker`, please install `docker` and `docker-compose` (see below if the last command fails):
```
$ sudo apt-get update
$ sudo apt-get install docker.io
$ sudo apt-get install docker-compose-plugin
```
If the latter command fails, you can install the compose plugin manually by executing the following:
```
$ DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
$ mkdir -p $DOCKER_CONFIG/cli-plugins
$ curl -SL https://github.com/docker/compose/releases/download/v2.23.0/docker-compose-linux-x86_64 -o $DOCKER_CONFIG/cli-plugins/docker-compose
$ chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose
```
Please see the following (documentation)[https://docs.docker.com/compose/install/linux/#install-using-the-repository] for more details if you would like to install a different version of Compose or if you want to install it for all users.

### Create Docker Group
If your system already has `docker` --- and you can run `docker` without using `sudo` --- then you can skip this step. Otherwise, you will need to create a docker group and add your user to it so that our installation script can run without assuming `sudo`.

First, create the docker group as follows:
```
$ sudo groupadd docker
```
Next, add your user to the docker group:
```
$ sudo usermod -aG docker $USER
```
You can then activate this change by either logging out and logging back in to your system, or by executing:
```
$ newgrp docker
```
For more details, please see the following (documentation)[https://docs.docker.com/engine/install/linux-postinstall/#manage-docker-as-a-non-root-user]

### Set Variables and Run Installation
Once `docker` and `docker-compose` are installed, you'll then need to provide your OpenAI API key and HuggingFace token to the services by placing them in files which will be used by `docker-compose`:
```
$ mkdir deploy/vanilla/secrets
$ echo $OPENAI_API_KEY > deploy/vanilla/secrets/openai_api_key.txt
$ echo $HF_TOKEN > deploy/vanilla/secrets/hf_token.txt
```
Compose will place these files into a special `/run/secrets` directory inside of the containers so that these values can be accessed at runtime.

Now you can run your installation by executing the following from the root of the A2rchi repository:
```
$ ./deploy/vanilla/install.sh
```

After `docker` builds and starts its images, you should eventually see something like the following:
```
[+] Running 3/3
 ✔ Network vanilla_default       Created
 ✔ Container vanilla-chromadb-1  Healthy
 ✔ Container vanilla-chat-1      Started         
```
Note that the `vanilla-chat-1` container will wait for the `vanilla-chromadb-1` container to become healthy before starting, in a process that should take ~10s after the latter's creation.

- you can now access the chat app by visiting (host:port)
- you can upload documents by doing xyz