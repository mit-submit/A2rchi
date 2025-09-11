# Install 

## System Requirements

A2rchi is deployed using a python-based CLI onto containers. It requires:

- `docker` version 24+ or `podman` version 5.4.0+ (for containers)
- `python 3.10.0+` (for CLI)

> Note: We support either running open source models locally, or connecting to existing APIs. If you plan to run open source models on your machine's GPUs, please check out the [Advanced Setup & Deployment](advanced_setup_deploy.md) section for more information.

## Install

Clone the a2rchi repo:
```nohighlight
git clone https://github.com/mit-submit/A2rchi.git
```
From the repository run:
```nohighlight
pip install -e .
```
This will install A2rchi's dependencies as well as a local CLI tool. You should be able to see that it is installed with
```nohighlight
which a2rchi
```
which will show the full path of the `a2rchi` executable.

<details>
<summary>Show Full Installation Script</summary>
<br>
You can use the following script to set up A2rchi from scratch. Copy and paste it into your terminal:

```bash
# Clone the repository
git clone https://github.com/mit-submit/A2rchi.git
cd A2rchi
export A2RCHI_DIR=$(pwd)

# (Optional) Create and activate a virtual environment
python3 -m venv .a2rchi_venv
source .a2rchi_venv/bin/activate

# Install dependencies
cd $A2RCHI_DIR
pip install -e .

# Verify installation
which a2rchi
```

</details>
<br>