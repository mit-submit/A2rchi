#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${CONTAINER_RUNTIME:-docker}"
TAG="${1:-}"

usage() {
  cat <<'EOF'
Usage: rebuild_and_push_docker_images.sh [TAG]

Rebuilds the base Docker images and pushes them to Docker Hub.
- TAG (optional): image tag to use; defaults to the project version from pyproject.toml.
Set CONTAINER_RUNTIME=docker|podman to override the container CLI (defaults to docker).
EOF
}

if [[ "${1:-}" =~ ^(-h|--help)$ ]]; then
  usage
  exit 0
fi

if [[ -z "$TAG" && -f "$ROOT_DIR/pyproject.toml" ]]; then
  TAG="$(awk -F'"' '/^version[[:space:]]*=/ {print $2; exit}' "$ROOT_DIR/pyproject.toml" || true)"
fi

if [[ -z "$TAG" ]]; then
  printf 'Error: unable to determine image tag.\n\n' >&2
  usage
  exit 1
fi

if ! command -v "$RUNTIME" >/dev/null 2>&1; then
  echo "Error: container runtime '$RUNTIME' not found in PATH." >&2
  exit 1
fi

echo "Using container runtime: $RUNTIME"
echo "Image tag: $TAG"

echo "Updating requirements files..."
cat "$ROOT_DIR/requirements/cpu-requirementsHEADER.txt" \
    "$ROOT_DIR/requirements/requirements-base.txt" \
    > "$ROOT_DIR/src/cli/templates/dockerfiles/base-python-image/requirements.txt"
cat "$ROOT_DIR/requirements/gpu-requirementsHEADER.txt" \
    "$ROOT_DIR/requirements/requirements-base.txt" \
    > "$ROOT_DIR/src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt"

declare -A IMAGE_DIRS=(
  ["a2rchi/a2rchi-python-base"]="$ROOT_DIR/src/cli/templates/dockerfiles/base-python-image"
  ["a2rchi/a2rchi-pytorch-base"]="$ROOT_DIR/src/cli/templates/dockerfiles/base-pytorch-image"
)

for image in "${!IMAGE_DIRS[@]}"; do
  build_context="${IMAGE_DIRS[$image]}"
  echo "Building $image:$TAG from $build_context"
  "$RUNTIME" build -t "$image:$TAG" "$build_context"
done

echo
echo "Logging in to Docker Hub (password prompt will follow)..."
"$RUNTIME" login docker.io

for image in "${!IMAGE_DIRS[@]}"; do
  echo "Pushing $image:$TAG"
  "$RUNTIME" push "$image:$TAG"
done

echo "All images pushed with tag $TAG."
