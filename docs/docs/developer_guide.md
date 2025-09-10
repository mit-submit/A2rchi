# Developers Guide

Below is all the information which developers may need in order to get started contributing to the A2rchi project.

## Editing Documentation.

Editing documentation requires you to install the mkdocs python packge:
```
pip install mkdocs
```
To edit documentation, simply make changes to the `.md` and `.yml` files in the `./docs` folder. To view your changes without pushing them, `cd` into the `./docs` folder and then run `mkdocs serve`. Add the `-a IP:HOST` argument (default is localhost:8000) to specify where to host the docs so you can easily view your changes on the web.

To publish your changes, run `mkdocs gh-deploy`. Please make sure to also open a PR to merge the documentation changes into main.

Note, please do NOT edit files in the gh-pages branch by hand, again, make a PR to main from a separate branch, and then you can deploy from main with the new changes.

## DockerHub Images

A2rchi will load from different base images hosted on dockerhub. If you do not need to use GPUs the python base image will be installed. Alternatively, the pytorch base image will be installed. 
The base Docker file used to make these base images from which the chat interface inherits its changes from can be found in `a2rchi/cli/templates/dockerfiles/base-X-image` directories.
They are currently hosted on dockerhub at the following links: 
pytorch: https://hub.docker.com/r/a2rchi/a2rchi-pytorch-base
python: https://hub.docker.com/r/a2rchi/a2rchi-python-base

In order to rebuild the base images from which the dockerfiles inherit, go to the `base-xxx-image` directory found in `templates/dockerfiles/`.
In these directories, there is a different set of requirements for each along with their license and respective dockerfiles.
To regenerate the requirements if they have been changed run the following commands to ensure that the right header is used for each: 

for the python image:
```
cat requirements/cpu-requirementsHEADER.txt requirements/requirements-base.txt > a2rchi/templates/dockerfiles/base-python-image/requirements.txt
```
for the pytorch image:
```
cat requirements/gpu-requirementsHEADER.txt requirements/requirements-base.txt > a2rchi/templates/dockerfiles/base-python-image/requirements.txt
```

Then while inside of the `templates/dockerfiles/base-xxx-image` directory, simply run the following command to build the image.  

```
podman build -t a2rchi/<image-name>:<tag> . 
```

after having checked that the newly built image is working, to update it on dockerhub, login to dockerhub using (ask for a senior developer for the password/master token),

```
podman login docker.io 
```

and finally push the image to the repository as such: 

```
podman push a2rchi/<image-name>:<tag> 
```


