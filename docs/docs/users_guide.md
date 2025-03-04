# Users Guide

A2rchi is built with several interfaces which collaborate with a CORE in order to create a customized RAG system. If you haven't already, read out `Getting Started` page to install, create, and run the CORE. 

The user's guide is broken up into detailing the various interfaces and the secrets/configurations needed for those interfaces. 

To include an interface, simply add it's tag at the end of the `create` CLI command. For example, to include grafana run
```
$ a2rchi create --name my-a2rchi --a2rchi-config example_conf.yaml --grafana True
```

## CORE Interface

TODO: add description of interface here

### Secrets

### Configuration

## Adding Documents and the Uploader Interface

### Adding Documents

There are two main ways to add documents to A2rchi's vector database. They are
·
- Adding lists of online pdf sources to the configuration to be uploaded at start up
- Manually adding files while the service is running via the uploader.

Both methods are outlined below

#### Document Lists

Before starting the a2rchi service, one can create a document list, which is a `.list` file containing *links* that point to either `html`, `txt`, or `pdf` files. `.list` files are also able to support comments,      ↪using "#". They are also generally stored in the `config` folder of the repository. For example, the below may be a list

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
--service-uploader
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

Once you have created an account, visit the outgoing port of the data manager docker service and then log in. The GUI will then allow you to upload documents while a2rchi is still running. Note that it may take a   ↪few minutes for all the documents to upload.


## Piazza Interface

TODO: add description of interface here

### Secrets

### Configuration

## Cleo/Mailbox Interface

TODO: add description of interface here

### Secrets

### Configuration

## Grafana Interface 

TODO: add description of interface here

### Secrets

### Configuration
