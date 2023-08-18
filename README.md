# A2rchi
An AI Augmented Research Chat Intelligence for MIT's subMIT project in the physics department

## Setup

### Keys and Passwords

A2rchi uses several other services in order to make it's operations possible. These include OpenAI, Cleo, and a mailbox manager. In order to use these services, A2rchi must be given acsess to the account usernames and passwords. You should add these as text files in a secure directory outside the A2rchi repository. You can find templates of what the text files look like in the config directory of the repo. These are then loaded as environment variables in the `setup.sh` script. You may have to modify the paths in `setup.sh` to fit the paths where you saved the keys and passwords. 

The `.imap`, `.sender`, and `.cleo ` file are only needed to run the mailbox/cleo service. The OpenAI key is needed to run the GPT-4 model which A2rchi is based on. However A2rchi is also able to run on other models (found in `chain/models.py`). The exact model to use can be changed in `config/config.yaml `. 

The `.salt` file is needed to establish a salt for secure hasing of passwords into the data uploader.

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

(You need not create the environment everytime you log in, but you do need to activate it)

### Vector Store (dynamic)

The core of A2rchi's knowledge is stored in a vectorstore. A2rchi comes with the ability to dynamically update her vectorstore without having to interupt the services. In order for this to work, there must be a http chromadb server set up. If you already have a http chromadb server running, simply add the host name and port of the server to the config. To set up a chromadb server:

- Make sure you have docker-compose installed and working. To do this, make sure that docker has been started on your machine and your username has been added to the users who are allowed to acsess docker.
- Clone the chromadb git repository, found here: https://github.com/chroma-core/chroma 
- Insider the git repo, run `docker-compose up -d --build`. By default, this will create a server on localhost port 8000

Once you have a chromadb server which you can connect to, you are able to acsess dynamic updating of the vectorstore. Make sure in the config that `use_HTTP_chromadb_client` is `True`. Note, your specific vectorstore will be named according to the `collection_name`. Two programs which are acsessing the same collection name under the same chromadb server will both modify the vectorstore. 

### Vector Store (static)

If you are not able to set up dynamic updating of the vectorstore, simply set `use_HTTP_chromadb_client` to `False`. This will force the vectorstore to be stored locally on your filesystem. However, due to write conflicts, we do not recommend you change the vectorstore while any services are using it. If possible, use a dynamic vectorstore.

## Usage

### Running Serivces

All the excecutables are in the `bin/` directory. Simply run them with python. You will need to run the data management service before anything else, otherwise A2rchi will not have any information to reference for context. The data management dumps data into the path specified in the config file, then creates a vectorstore. If the vectorstore is dynamic, the service should be allowed to continue in the background as it deploys an uploader application to manually manage context as well as constantly refreshes the vectorstore. If the vectorstore is static, the service will halt after all the data has been scraped. 


### Running Tests

All tests are done using pytest. In the top level directory of the repo, run the command `pytest`. 
