# Adding Documents

There are two main ways to add documents to A2rchi's vector database. They are
 
- Adding lists of online pdf sources to the configuration to be uploaded at start up
- Manually adding files while the service is running via the uploader.

Both methods are outlined below

## Document Lists

Before starting the a2rchi service, one can create a document list, which is a `.list` file containing links that point to either `html`, `txt`, or `pdf` files. `.list` files are also able to support comments, using "#". They are also generally stored in the `config` folder of the repository. For example, the below may be a list

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

## Manual Uploader

In order to upload papers while a2rchi is running via an easily accessible GUI, use the data manager built into the system. The manager can be found as a docker service. The exact port may vary based on configuration (default is `5001`). A simple `docker ps -a` command run on the server will inform which port it's being run on.

In order to access the manager, one must first make an account. To do this, first get the container ID of the uploader container using `docker ps -a`. Then, acces the container using
```
docker exec -it <CONTAINER-ID> bash
```
so you can run
```
python bin/service_create_account.py
```
from the `/root/A2rchi/a2rchi` directory. 

This script will guide you through creating an account. Note that we do not garuntee the security of this account, so never upload critical passwords to create it. 

Once you have created an account, visit the outgoing port of the data manager docker service and then log in. The GUI will then allow you to upload documents while a2rchi is still running. Note that it may take a few minutes for all the documents to upload.