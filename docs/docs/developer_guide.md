# Developers Guide

Below is all the information developers may need to get started contributing to the A2rchi project.

## Editing Documentation

Editing documentation requires the `mkdocs` Python package:

```bash
pip install mkdocs
```

To edit documentation, update the `.md` and `.yml` files in the `./docs` folder. To preview changes locally, run:

```bash
cd docs
mkdocs serve
```

Add the `-a IP:HOST` argument (default is `localhost:8000`) to specify the host and port.

Publish your changes with:

```bash
mkdocs gh-deploy
```

Always open a PR to merge documentation changes into `main`. Do not edit files directly in the `gh-pages` branch.

## DockerHub Images

A2rchi loads different base images hosted on Docker Hub. The Python base image is used when GPUs are not required; otherwise the PyTorch base image is used. The Dockerfiles for these base images live in `src/cli/templates/dockerfiles/base-X-image`.

Images are hosted at:

- Python: <https://hub.docker.com/r/a2rchi/a2rchi-python-base>
- PyTorch: <https://hub.docker.com/r/a2rchi/a2rchi-pytorch-base>

To rebuild a base image, navigate to the relevant `base-xxx-image` directory under `src/cli/templates/dockerfiles/`. Each directory contains the Dockerfile, requirements, and license information.

Regenerate the requirements files with:

```bash
# Python image
cat requirements/cpu-requirementsHEADER.txt requirements/requirements-base.txt > src/cli/templates/dockerfiles/base-python-image/requirements.txt

# PyTorch image
cat requirements/gpu-requirementsHEADER.txt requirements/requirements-base.txt > src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt
```

Build the image:

```bash
podman build -t a2rchi/<image-name>:<tag> .
```

After verifying the image, log in to Docker Hub (ask a senior developer for credentials):

```bash
podman login docker.io
```

Push the image:

```bash
podman push a2rchi/<image-name>:<tag>
```
