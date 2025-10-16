#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/docker_common.sh"

RUNTIME="${CONTAINER_RUNTIME:-docker}"
TAG_INPUT="${1:-}"

usage() {
  cat <<'USAGE'
Usage: push_docker_images.sh [TAG]

Pushes previously built Docker images to Docker Hub.
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

for image in "${!IMAGE_DIRS[@]}"; do
  for tag in "$TAG" latest; do
    if ! "$RUNTIME" image inspect "$image:$tag" >/dev/null 2>&1; then
      echo "Error: image $image:$tag not found locally. Build it before pushing." >&2
      exit 1
    fi
  done
done

docker_login "$RUNTIME"

for image in "${!IMAGE_DIRS[@]}"; do
  for tag in "$TAG" latest; do
    echo "Pushing $image:$tag"
    "$RUNTIME" push "$image:$tag"
  done
done

echo "All images pushed with tags $TAG and latest."
