#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/publish-dockerhub.sh -u <dockerhub-user-or-org> [-v <version>] [options]

Options:
  -u  Docker Hub namespace, for example: myname or myorg
  -v  Image version tag. Default: current git tag, short commit, or yyyyMMddHHmmss
  -p  Platforms for buildx. Default: linux/amd64,linux/arm64
  -l  Also tag and push latest. Default: enabled
  -n  Do not push latest
  -h  Show help

Environment:
  BACKEND_IMAGE   Backend repository name. Default: budmon-backend
  FRONTEND_IMAGE  Frontend repository name. Default: budmon-frontend

Examples:
  docker login
  scripts/publish-dockerhub.sh -u mydockerhub -v 1.0.0
  scripts/publish-dockerhub.sh -u mydockerhub -v 1.0.0 -p linux/amd64
EOF
}

DOCKER_NAMESPACE=""
VERSION=""
PLATFORMS="linux/amd64,linux/arm64"
PUSH_LATEST=1
BACKEND_IMAGE="${BACKEND_IMAGE:-budmon-backend}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-budmon-frontend}"

while getopts ":u:v:p:lnh" opt; do
  case "$opt" in
    u) DOCKER_NAMESPACE="$OPTARG" ;;
    v) VERSION="$OPTARG" ;;
    p) PLATFORMS="$OPTARG" ;;
    l) PUSH_LATEST=1 ;;
    n) PUSH_LATEST=0 ;;
    h) usage; exit 0 ;;
    :) echo "Missing value for -$OPTARG" >&2; usage; exit 2 ;;
    \?) echo "Unknown option: -$OPTARG" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$DOCKER_NAMESPACE" ]]; then
  echo "Docker Hub namespace is required." >&2
  usage
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running or current user cannot access it." >&2
  exit 1
fi

if [[ -z "$VERSION" ]]; then
  if git describe --tags --exact-match >/dev/null 2>&1; then
    VERSION="$(git describe --tags --exact-match)"
  elif git rev-parse --short HEAD >/dev/null 2>&1; then
    VERSION="$(git rev-parse --short HEAD)"
  else
    VERSION="$(date +%Y%m%d%H%M%S)"
  fi
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_REPO="${DOCKER_NAMESPACE}/${BACKEND_IMAGE}"
FRONTEND_REPO="${DOCKER_NAMESPACE}/${FRONTEND_IMAGE}"

ensure_builder() {
  if ! docker buildx inspect budmon-builder >/dev/null 2>&1; then
    docker buildx create --name budmon-builder --use >/dev/null
  else
    docker buildx use budmon-builder >/dev/null
  fi
}

build_and_push() {
  local context="$1"
  local repo="$2"
  local name="$3"
  local latest_args=()

  if [[ "$PUSH_LATEST" -eq 1 ]]; then
    latest_args=(-t "${repo}:latest")
  fi

  echo "==> Building and pushing ${name}"
  echo "    repo: ${repo}"
  echo "    version: ${VERSION}"
  echo "    platforms: ${PLATFORMS}"

  docker buildx build \
    --platform "$PLATFORMS" \
    -t "${repo}:${VERSION}" \
    "${latest_args[@]}" \
    --push \
    "$context"
}

echo "Publishing BudMon images to Docker Hub"
echo "Namespace: ${DOCKER_NAMESPACE}"
echo "Backend:   ${BACKEND_REPO}:${VERSION}"
echo "Frontend:  ${FRONTEND_REPO}:${VERSION}"
if [[ "$PUSH_LATEST" -eq 1 ]]; then
  echo "Latest:    enabled"
else
  echo "Latest:    disabled"
fi

ensure_builder
build_and_push "${ROOT_DIR}/backend" "$BACKEND_REPO" "backend"
build_and_push "${ROOT_DIR}/frontend" "$FRONTEND_REPO" "frontend"

echo "Done."
