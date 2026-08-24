<p align="center">
  <img src="https://raw.githubusercontent.com/archi-physics/archi/main/docs/docs/_static/archi-logo.png" width="200" />
</p>

# Archi — the HEP distribution of OKG

[![Docs](https://img.shields.io/badge/docs-online-blue)](https://archi-physics.github.io/archi/)

Archi is the HEP distribution of OKG: connectors, enrichers, live tools,
playbooks, schemas, and bundles, shipped as the pip wheel `archi` built from
[`python/`](python/). It contains no credentials, no site config, and no
running services — it is installed onto an OKG deployment via bundles
(e.g. `okg install --profile cern-team`). Instances live in their own
repositories and carry only configuration and secrets.

## Repository layout

- [`python/`](python/) — the `archi` package (wheel source, tests).
- [`bundles/`](bundles/) — install bundles (e.g. [`cern-team`](bundles/cern-team/)).
- [`skills/`](skills/) — playbooks for agents operating over the graph.
- [`docs/`](docs/) — program docs and the MkDocs site.
- [`pact/`](pact/) — PACT change ledger (requirements, tasks, evidence).

## Key documents

- [Program spec (ADR 0001)](docs/adr/0001-archi-v3-program-spec.md) — the
  Archi v3 program: architecture, decisions, waves.
- [OKG alignment page](docs/okg-alignment.md) — the OKG-side sync page:
  current state, consumed substrate surface, issue mapping.
- [cern-team install demo](docs/cern-team-demo.md) — reproducible runbook
  for the end-to-end bundle install.

## v2

The v2 application (the RAG chatbot) lives on the `main` branch and serves
existing deployments until cutover; this branch line carries v3 only.

## License

Archi is released under the [MIT License](LICENSE). For project inquiries,
contact paus@mit.edu.
