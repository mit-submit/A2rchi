# Developers Guide

Below is all the information developers may need to get started contributing to the A2RCHI project.

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

A2RCHI loads different base images hosted on Docker Hub. The Python base image is used when GPUs are not required; otherwise the PyTorch base image is used. The Dockerfiles for these base images live in `src/cli/templates/dockerfiles/base-X-image`.

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

## Data Ingestion Architecture

A2RCHI ingests content through **sources** which are collected by **collectors** (`data_manager/collectors`).
These documents are written to persistent, local files via the `PersistenceService`, which uses `Resource` objects as an abstraction for different content types, and `ResourceMetadata` for associated metadata.
A catalog of persisted files is maintained in `index.yaml` and `metadata_index.yaml`.
Finally, the `VectorStoreManager` reads these files, splits them into chunks, generates embeddings, and indexes them in ChromaDB.

### Resources and `BaseResource`

Every collected artifact from the collectors is represented as a subclass of `BaseResource` (`src/data_manager/collectors/resource_base.py`). Subclasses must implement:

- `get_hash()`: a stable identifier used as the key in the filesystem catalog.
- `get_filename()`: the on-disk file name (including extension).
- `get_content()`: returns the textual or binary payload that should be persisted.

Resources may optionally override:

- `get_metadata()`: returns a metadata object (typically `ResourceMetadata`) describing the item. Keys should be serialisable strings and are flattened into the vector store metadata.
- `get_metadata_path()`: inherited helper that derives a `.meta.yaml` path when metadata is present.

`ResourceMetadata` (`src/data_manager/collectors/utils/metadata.py`) enforces a required `display_name` and normalises the `extra` dictionary so all values become strings. Use this to expose source-specific information such as URLs, ticket identifiers, or visibility flags.

The guiding philosophy is that **resources describe content**, but never write to disk themselves. This separation keeps collectors simple, testable, and ensures consistent validation when persisting different resource types.

### Persistence Service

`PersistenceService` (`src/data_manager/collectors/persistence.py`) centralises all filesystem writes for both the document content, and its metadata. When `persist_resource()` is called it:

1. Resolves the target path under the configured `DATA_PATH`.
2. Validates and writes the resource content (rejecting empty payloads or unknown types).
3. Serialises metadata, if provided, to an adjacent `*.meta.yaml` file.
4. Updates two catalogs:
   - `index.yaml`: maps each resource hash to the content file path.
   - `metadata_index.yaml`: maps resource hashes to the metadata file path.

Collectors only interact with `PersistenceService`; they should not touch the filesystem directly.

### Vector Database

The vector store lives under the `data_manager/vectorstore` package. `VectorStoreManager` reads the persistence catalogs and synchronises them with ChromaDB:

1. Loads the tracked files and metadata hashes from `index.yaml`.
2. Splits documents into chunks, optional stemming, and builds embeddings via the configured model.
3. Adds chunks to the Chroma collection with flattened metadata (including resource hash, filename, human-readable display fields, and any source-specific extras).
4. Deletes stale entries when the underlying files disappear or are superseded.

Because the manager defers to the catalog, any resource persisted through `PersistenceService` automatically becomes eligible for indexing—no extra plumbing is required.

## Extending the stack

When integrating a new source, create a collector under `data_manager/collectors`. Collectors should yield `Resource` objects. A new `Resource` subclass is only needed if the content type is not already represented (e.g., text, HTML, markdown, images, etc.), but it must implement the required methods described above.

When integrating a new collector, ensure that any per-source configuration is encoded in the resource metadata so downstream consumers—such as the chat app—can honour it.

When extending the embedding pipeline or storage schema, keep this flow in mind: collectors produce resources → `PersistenceService` writes files and updates indexes → `VectorStoreManager` promotes the indexed files into Chroma. Keeping responsibilities narrowly scoped makes the ingestion stack easier to reason about and evolve.
