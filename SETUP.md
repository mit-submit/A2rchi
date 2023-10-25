## Getting Started with A2rchi
These instructions were validated on an AWS EC2 c5d.large (2 vCPU, 8GB memory) running an Ubuntu 22.04 AMI.

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
Please see the following page for more details if you would like to install a different version of Compose or if you want to install it for all users: https://docs.docker.com/compose/install/linux/#install-using-the-repository.

### TODO