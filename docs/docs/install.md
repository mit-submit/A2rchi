# Install

## System Requirements

A2RCHI is deployed using a Python-based CLI onto containers. It requires:

- `docker` version 24+ or `podman` version 5.4.0+ (for containers)
- `python 3.10.0+` (for the CLI)

> **Note:** We support either running open-source models locally or connecting to existing APIs. If you plan to run open-source models on your machine's GPUs, see the [Advanced Setup & Deployment](advanced_setup_deploy.md) section.

## Installation

Clone the A2RCHI repository:

```bash
git clone https://github.com/mit-submit/A2rchi.git
```

Check out the latest stable tag:

```bash
cd A2rchi
git checkout $(git describe --tags $(git rev-list --tags --max-count=1))
```

Install A2RCHI (from inside the repository):

```bash
pip install -e .
```

This installs A2RCHI's dependencies and the CLI tool. Verify the installation with:

```bash
which a2rchi
```

The command prints the path to the `a2rchi` executable.

<details>
<summary>Show Full Installation Script</summary>

```bash
# Clone the repository
git clone https://github.com/mit-submit/A2rchi.git
cd A2rchi
export A2RCHI_DIR=$(pwd)

# (Optional) Checkout the latest stable tag
git checkout $(git describe --tags $(git rev-list --tags --max-count=1))

# (Optional) Create and activate a virtual environment
python3 -m venv .a2rchi_venv
source .a2rchi_venv/bin/activate

# Install dependencies
cd "$A2RCHI_DIR"
pip install -e .

# Verify installation
which a2rchi
```

</details>
