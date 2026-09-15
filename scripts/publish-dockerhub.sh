#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/publish-dockerhub.sh -u <dockerhub-user-or-org> [-v <version>] [options]

Options:
  -u  Docker Hub namespace, for example: myname or myorg
  -v  Image version tag. Default: repository VERSION file (1.0.2)
  -p  Platforms for buildx. Default: linux/amd64,linux/arm64
  -x  Build proxy URL. Default: http://127.0.0.1:7890 (Docker Desktop)
  -d  Disable explicit proxy settings for this invocation
  -l  Also tag and push latest. Default: enabled
  -n  Do not push latest
  -h  Show help

Environment:
  BACKEND_IMAGE   Backend repository name. Default: budmon-backend
  FRONTEND_IMAGE  Frontend repository name. Default: budmon-frontend
  BUDMON_BUILD_PROXY  Override default proxy; empty disables explicit proxy settings

Examples:
  docker login
  scripts/publish-dockerhub.sh -u mydockerhub -v 1.0.2
  scripts/publish-dockerhub.sh -u mydockerhub -v 1.0.2 -p linux/amd64
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_NAMESPACE=""
VERSION=""
PLATFORMS="linux/amd64,linux/arm64"
PUSH_LATEST=1
BUILD_PROXY="${BUDMON_BUILD_PROXY-http://127.0.0.1:7890}"
BUILDER_NAME="budmon-builder"
BACKEND_IMAGE="${BACKEND_IMAGE:-budmon-backend}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-budmon-frontend}"

while getopts ":u:v:p:x:dlnh" opt; do
  case "$opt" in
    u) DOCKER_NAMESPACE="$OPTARG" ;;
    v) VERSION="$OPTARG" ;;
    p) PLATFORMS="$OPTARG" ;;
    x) BUILD_PROXY="$OPTARG" ;;
    d) BUILD_PROXY="" ;;
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
  VERSION="$(cat "${ROOT_DIR}/VERSION")"
fi
if [[ ! "$VERSION" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  echo "Invalid Docker image version: $VERSION" >&2
  exit 2
fi

BACKEND_REPO="${DOCKER_NAMESPACE}/${BACKEND_IMAGE}"
FRONTEND_REPO="${DOCKER_NAMESPACE}/${FRONTEND_IMAGE}"

# CLI-side registry authentication runs on the host; BuildKit and RUN steps
# run in containers and need Docker Desktop's host gateway instead of loopback.
CONTAINER_PROXY="$BUILD_PROXY"
if [[ -n "$BUILD_PROXY" ]]; then
  case "$BUILD_PROXY" in
    http://*|https://*) ;;
    *) echo "Proxy must use http:// or https://" >&2; exit 2 ;;
  esac
  CONTAINER_PROXY="${CONTAINER_PROXY/127.0.0.1/host.docker.internal}"
  CONTAINER_PROXY="${CONTAINER_PROXY/localhost/host.docker.internal}"
  export HTTP_PROXY="$BUILD_PROXY" HTTPS_PROXY="$BUILD_PROXY"
  export http_proxy="$BUILD_PROXY" https_proxy="$BUILD_PROXY"
  export ALL_PROXY="$BUILD_PROXY" all_proxy="$BUILD_PROXY"
  export NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"
  # A separate builder preserves existing builders/caches and avoids stale proxy settings.
  proxy_id="$(printf '%s' "$CONTAINER_PROXY" | cksum | awk '{print $1}')"
  BUILDER_NAME="budmon-proxy-${proxy_id}"
fi

ensure_builder() {
  if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
    local create_args=(buildx create --name "$BUILDER_NAME" --driver docker-container)
    if [[ -n "$BUILD_PROXY" ]]; then
      local key
      for key in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
        create_args+=(--driver-opt "env.${key}=${CONTAINER_PROXY}")
      done
      create_args+=(--driver-opt "env.NO_PROXY=localhost"
                    --driver-opt "env.no_proxy=localhost")
    fi
    docker "${create_args[@]}" >/dev/null
  fi
  docker buildx inspect "$BUILDER_NAME" --bootstrap >/dev/null
}

build_and_push() {
  local context="$1"
  local repo="$2"
  local name="$3"
  local build_args=(buildx build --builder "$BUILDER_NAME" --platform "$PLATFORMS" -t "${repo}:${VERSION}")

  if [[ "$PUSH_LATEST" -eq 1 ]]; then
    build_args+=(-t "${repo}:latest")
  fi

  if [[ -n "$BUILD_PROXY" ]]; then
    local key
    for key in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
      build_args+=(--build-arg "${key}=${CONTAINER_PROXY}")
    done
    build_args+=(--build-arg "NO_PROXY=localhost,127.0.0.1,::1"
                 --build-arg "no_proxy=localhost,127.0.0.1,::1")
  fi

  echo "==> Building and pushing ${name}"
  echo "    repo: ${repo}"
  echo "    version: ${VERSION}"
  echo "    platforms: ${PLATFORMS}"

  docker "${build_args[@]}" --push "$context"
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

echo "Builder:   ${BUILDER_NAME}"
if [[ -n "$BUILD_PROXY" ]]; then echo "Proxy:     enabled for CLI, BuildKit, and dependency installation"; fi
ensure_builder
build_and_push "${ROOT_DIR}/backend" "$BACKEND_REPO" "backend"
build_and_push "${ROOT_DIR}/frontend" "$FRONTEND_REPO" "frontend"

echo "Done."
