# A2rchi

An AI Augmented Research Chat Intelligence for MIT's subMIT project in the physics department

## Overview

A2rchi is designed as a bunch of services that enables the user to conveniently build and use a highly customized AI. The basis to the AI starts from a Large Language Modal (LLM), like GPT-4 or LLama2, and refines its answers using the data the user has selected to upload. In its work on the given question and context she, A2rchi, will follow the prompts that the user can freely specify. A feedback loop using answers that the user can certify is available so to tune A2rchi's knowledge on the fly. 

## Setup

### Keys and Passwords

A2rchi uses several other services in order to make it's operations possible. These include OpenAI, Cleo, and a mailbox manager. In order to use these services, A2rchi must be given access to the account usernames and passwords. You should add these as text files in a secure directory outside the A2rchi repository. You can find templates of what the text files look like in the config directory of the repo. These are then loaded as environment variables in the `setup.sh` script. You may have to modify the paths in `setup.sh` to fit the paths where you saved the keys and passwords. 

The `.imap`, `.sender`, and `.cleo` files are only needed to run the mailbox/cleo service. The OpenAI key is needed to run the GPT-4 model which A2rchi is based on. However A2rchi is also able to run on other models (found in `chain/models.py`). The exact model to use can be changed in `config/config.yaml `. 

The `.salt` file is needed to establish a salt for secure hashing of passwords into the data uploader.

Once all the account credentials are loaded into the places they need to be, simply do a base install and setup

```
./install.sh
source ./setup.sh
```

The install will generate the setup.sh script depending on the directory where A2rchi has been installed. It can be easily repeated when the software is moved. The setup.sh script will have to be sourced anytime you want to work with A2rchi.

```
source ./setup.sh
```

### Conda Environment

The environment.yml file contains the needed requirements to run A2rchi. To create the A2rchi environment, simply run

```
conda env create -f environment.yml -n "A2rchi_env"
```

in the repository (this may take awhile). Then activate it using

```
conda activate A2rchi_env
```

You do not need to create the environment every time you log in, but you do need to activate it.

### Alternatively to conda

You can can live dangerously if for whatever reason you do not like conda. You can go python barebones (pip). No guarantees for success, but you can skip conda.

```
pip install wheel interfaces torch langchain chromadb tiktoken flask_cors 
```

### Vector Store (dynamic)

A prerequisite for installing the chroma database and run it on your machine is to have docker installed and configured in such a way that you can use it as a non-root user. First docker has to be installed. For a newish (37) fedora this would be following the [instructions](https://docs.docker.com/engine/install/fedora/)

```
sudo dnf remove docker \
              docker-client \
              docker-client-latest \
              docker-common \
              docker-latest \
              docker-latest-logrotate \
              docker-logrotate \
              docker-selinux \
              docker-engine-selinux \
              docker-engine
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-compose
sudo systemctl start docker
sudo systemctl enable docker
```

To run as rootless user a group called 'docker' has to exist:

```
sudo groupadd docker
```

and you have to be part of that group

```
sudo usermod -a -G docker $USER
```

Tests carefully whether this has already taken effect by using the 'groups' command to indicate your membership in the 'docker' group. You might have to logout or even reboot. These commands do work.

The core of A2rchi's knowledge is stored in a vectorstore. A2rchi comes with the ability to dynamically update her vectorstore without having to interrupt the services. In order for this to work, there must be a http chromadb server set up. If you already have a http chromadb server running, simply add the host name and port of the server to the config. Clone the chromadb git repository, build the image and start the chroma db server:
   
```
git clone https://github.com/chroma-core/chroma
cd chroma
docker-compose up -d --build
```

This will create a server on localhost port 8000, the default.

Once you have a chromadb server which you can connect to, you are able to access dynamic updating of the vectorstore. Make sure in the configuration in your config/config.yaml has `use_HTTP_chromadb_client` as `True`. Note, your specific vectorstore will be named according to the `collection_name`. Two programs which are accessing the same collection name under the same chromadb server will both modify the vectorstore. 

### Vector Store (static)

If you are not able to set up dynamic updating of the vectorstore, simply set `use_HTTP_chromadb_client` to `False`. This will force the vectorstore to be stored locally on your file system. However, due to write conflicts, we do not recommend you change the vectorstore while any services are using it. If possible, use a dynamic vectorstore.

## Usage

### Running Services

All the excecutables are in the `bin/` directory. Simply run them with python. You will need to run the data management service before anything else, otherwise A2rchi will not have any information to reference for context. The data management dumps data into the path specified in the config file, then creates a vectorstore. If the vectorstore is dynamic, the service should be allowed to continue in the background as it deploys an uploader application to manually manage context as well as constantly refreshes the vectorstore. If the vectorstore is static, the service will halt after all the data has been scraped. 

In order to access the uploading app, you will need to log in with a username and password. To create usernames and passwords, simply run the `create_account` service in the bin directory. If you change the `.salt` it will need to be redone because the passwords are salted.

```
./bin/service_create_account.py
```

### Running Tests

All tests are done using pytest. In the top level directory of the repo, run the command `pytest`. 
