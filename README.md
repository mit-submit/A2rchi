<p float="center">
  <img src="https://raw.githubusercontent.com/mit-submit/A2rchi/reorganization_and_sources/docs/docs/_static/a2rchi_logo.png" width="200"/>
</p>

# A2RCHI - AI Augmented Research Chat Intelligence

[![CI](https://github.com/mit-submit/A2rchi/actions/workflows/pr-preview.yml/badge.svg)](https://github.com/mit-submit/A2rchi/actions/workflows/pr-preview.yml)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://mit-submit.github.io/a2rchi/)

A2RCHI is a retrieval-augmented generation framework for research and education teams who need a low-barrier to entry, private, and extensible assistant. The system was first developed at MIT for the SubMIT computing project, and now powers chat, ticketing, and course-support workflows across academia and research organizations.

## Key Capabilities

A2RCHI provides:
- Several data ingestion connectors: Piazza, Slack, Discourse, email, web links, files, JIRA, and Redmine.
- Several interfaces: a chat app, ticketing assistant, email bot, and more.
- Customizable AI pipelines that combine retrieval, LLMs, and tools.
- Support for running or interacting with local and API-based LLMs.
- Modular design that allows custom data sources, LLM backends, and deployment targets.
- Containerized services and CLI utilities for repeatable deployments.

## Documentation

The [docs](https://mit-submit.github.io/a2rchi/) are organized as follows:

- [Install](https://mit-submit.github.io/a2rchi/install/) — system requirements and environment preparation.
- [Quickstart](https://mit-submit.github.io/a2rchi/quickstart/) — after installation, learn how to deploy your first A2RCHI instance.
- [User Guide](https://mit-submit.github.io/a2rchi/user_guide/) — framework concepts for users and administrators.
- [Advanced Setup & Deployment](https://mit-submit.github.io/a2rchi/advanced_setup_deploy/) — configuring A2RCHI for GPU use, custom models, and advanced workflows.
- [API Reference](https://mit-submit.github.io/a2rchi/api_reference/) — programmatic interfaces and integration points.
- [Developer Guide](https://mit-submit.github.io/a2rchi/developer_guide/) — codebase layout, contribution workflow, and extension patterns.

## Getting Started

Follow the [Install](https://mit-submit.github.io/a2rchi/install/) and [Quickstart](https://mit-submit.github.io/a2rchi/quickstart/) guide to set up prerequisites, configure data sources, and launch an instance.

## Contributing

We welcome fixes and new integrations—see the [Developer Guide](https://mit-submit.github.io/a2rchi/developer_guide/) for coding standards, testing instructions, and contribution tips. Please open issues or pull requests on the [GitHub repository](https://github.com/mit-submit/A2rchi).

## License and Support

A2RCHI is released under the [MIT License](LICENSE). For project inquiries, contact paus@mit.edu.
