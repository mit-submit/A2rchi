#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/docker_common.sh"

RUNTIME="${CONTAINER_RUNTIME:-docker}"
TAG_INPUT="${1:-}"

usage() {
  cat <<'USAGE'
Usage: build_docker_images.sh [TAG]

Builds the base Docker images locally.
- TAG (optional): image tag to use; defaults to the project version from pyproject.toml.
Set CONTAINER_RUNTIME=docker|podman to override the container CLI (defaults to docker).
USAGE
}

if [[ "${1:-}" =~ ^(-h|--help)$ ]]; then
  usage
  exit 0
fi

TAG="$(resolve_tag "$TAG_INPUT" || true)"
if [[ -z "$TAG" ]]; then
  echo "Error: unable to determine image tag." >&2
  usage
  exit 1
fi

ensure_runtime "$RUNTIME"

echo "Using container runtime: $RUNTIME"
echo "Image tag: $TAG"

echo "Updating requirements files..."
cat "$ROOT_DIR/requirements/cpu-requirementsHEADER.txt" \
    "$ROOT_DIR/requirements/requirements-base.txt" \
    > "$ROOT_DIR/src/cli/templates/dockerfiles/base-python-image/requirements.txt"
cat "$ROOT_DIR/requirements/gpu-requirementsHEADER.txt" \
    "$ROOT_DIR/requirements/requirements-base.txt" \
    > "$ROOT_DIR/src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt"

for image in "${!IMAGE_DIRS[@]}"; do
  build_context="${IMAGE_DIRS[$image]}"
  echo "Building $image:$TAG from $build_context"
  "$RUNTIME" build -t "$image:$TAG" -t "$image:latest" "$build_context"
  echo "Tagged $image:latest"
done

echo "All images built with tag $TAG."
