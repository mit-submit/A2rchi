"""Archi — the HEP distribution of OKG.

Ships the HEP schemas, source adapters, enrichment, live tools, skills,
bundles, and evaluation suite that turn a blank OKG install into a
working HEP instance (ADR 0001). Holds no credentials, no site
configuration, and no running services.

okg is a host dependency, deliberately not declared here: adapters are
imported *by* an OKG deployment (`module: archi.sources.<name>` in its
source registry), so the substrate is always already present.
"""

__version__ = "3.0.0a1"
