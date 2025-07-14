# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A2rchi (AI Augmented Research Chat Intelligence) is a RAG-based chat system originally developed for the subMIT project at MIT. It provides a containerized deployment system with multiple interfaces including chat, document uploading, grading services, and monitoring dashboards.

## Development Commands

### Installation and Setup
```bash
# Install the package in development mode
pip install .

# Basic installation using install script
./install.sh

# Create A2rchi deployment using CLI
a2rchi create --name <deployment_name> --a2rchi-config ./configs/example_conf.yaml
```

### Testing
```bash
# Run tests using pytest
python -m pytest test/

# Run specific test files
python -m pytest test/test_chains.py
python -m pytest test/test_interfaces.py
```

### Docker/Podman Operations
```bash
# Build and start services with Docker
docker compose -f <path>/compose.yaml up -d --build

# Build and start services with Podman
podman compose -f <path>/compose.yaml up -d --build

# Stop and remove deployment
a2rchi delete --name <deployment_name>
```

### Configuration Management
```bash
# Update existing deployment configuration
a2rchi update --name <deployment_name> --a2rchi-config <config_file>
```

## Architecture Overview

### Core Components

1. **CLI System** (`a2rchi/cli/`): Command-line interface for deployment management using Click framework
2. **Chain System** (`a2rchi/chains/`): LangChain-based RAG implementation with multiple model support (OpenAI, Anthropic, HuggingFace)
3. **Interfaces** (`a2rchi/interfaces/`): Multiple web interfaces including:
   - Chat app (Flask-based chat interface)
   - Grader app (Assessment and grading interface)
   - Uploader app (Document management interface)
4. **Utilities** (`a2rchi/utils/`): Core utilities for configuration, data management, embeddings, and database operations

### Service Architecture

The system deploys as containerized services:
- **Chat Service**: Main user interface for interacting with the RAG system
- **ChromaDB**: Vector database for document embeddings and similarity search
- **PostgreSQL**: Conversation history and metadata storage
- **Grafana** (optional): Monitoring and analytics dashboard
- **Grader Service** (optional): Automated assessment system

### Configuration System

- Configuration uses YAML files (see `configs/example_conf.yaml`)
- Template-based deployment with Jinja2 rendering
- Secrets management through dedicated directories
- GPU support for local model inference

### Key Design Patterns

- **Template-based deployment**: Uses Jinja2 templates for Docker Compose and configuration files
- **Modular architecture**: Services can be enabled/disabled through CLI flags
- **Multi-model support**: Abstracts different LLM providers (OpenAI, Anthropic, local models)
- **Vector store integration**: ChromaDB for semantic search and retrieval

## Important File Locations

- Configuration templates: `a2rchi/templates/`
- Deployment configs: `configs/`
- Test files: `test/`
- CLI entry point: `a2rchi/cli/cli_main.py`
- Main chain logic: `a2rchi/chains/chain.py`
- Chat interface: `a2rchi/interfaces/chat_app/app.py`

## Development Notes

- The system uses both Docker and Podman for containerization
- **Podman Version Support**: Supports podman 4.9.4+ with automatic version detection
  - Podman 4.9.4 - 5.3.x: Uses `podman-compose` for compatibility
  - Podman 5.4.0+: Uses native `podman compose` with improved GPU support
- GPU support is available for local model inference (configured via `--gpu` or `--gpu-ids`)
- Configuration validation ensures required fields are present before deployment
- Service orchestration handles dependencies (e.g., ChromaDB must be healthy before chat service starts)
- The CLI creates deployment directories under `~/.a2rchi/` for each named instance