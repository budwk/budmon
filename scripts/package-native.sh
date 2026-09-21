#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

usage() {
    cat <<'HELP'
Usage: bash scripts/package-native.sh [-o OUTPUT_DIR]
Build the frontend locally and create a portable native deployment archive.
  -o OUTPUT_DIR  Default: repository dist directory
  -h, --help     Show this help
Requires Node.js >=20, npm, rsync and tar. No Python runtime is bundled.
HELP
}
fail() { echo "Error: $*" >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output_dir="$source_dir/dist"
while (( $# )); do
    case "$1" in
        -o)
            (( $# >= 2 )) || fail "Missing value for $1"
            output_dir=$2
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Unknown argument: $1" ;;
    esac
done
for cmd in node npm rsync tar; do
    command -v "$cmd" >/dev/null || fail "Missing command: $cmd"
done
node -e 'if (Number(process.versions.node.split(".")[0]) < 20) { throw Error("Node.js >=20 required") }'
stage=$(mktemp -d "${TMPDIR:-/tmp}/budmon-package.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
trap 'echo "Packaging failed; no new archive was published." >&2' ERR
mkdir -p "$stage/frontend/src"
cp "$source_dir/frontend/package.json" "$source_dir/frontend/index.html" "$source_dir/frontend/vite.config.js" "$stage/frontend/"
if [[ -f "$source_dir/frontend/package-lock.json" ]]; then
    cp "$source_dir/frontend/package-lock.json" "$stage/frontend/"
fi
rsync -a "$source_dir/frontend/src/" "$stage/frontend/src/"
if [[ -d "$source_dir/frontend/public" ]]; then
    rsync -a "$source_dir/frontend/public/" "$stage/frontend/public/"
fi
(
    cd "$stage/frontend"
    if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
    npm run build
)
[[ -f "$stage/frontend/dist/index.html" ]] || fail 'Frontend build did not produce index.html.'
[[ -f "$source_dir/website/dist/index.html" ]] || fail 'Missing website/dist/index.html.'
name=budmon
release_dir="$stage/$name"
mkdir -p "$release_dir/backend/app" "$release_dir/frontend/dist" "$release_dir/scripts" \
    "$release_dir/deploy/systemd" "$release_dir/deploy/nginx" "$release_dir/website/dist" "$release_dir/docs"
# Copy only runtime code and explicit deployment files, never local data/secrets/venvs.
rsync -a --prune-empty-dirs --exclude='__pycache__/' --include='*/' --include='*.py' --include='*.cer' --exclude='*' \
    "$source_dir/backend/app/" "$release_dir/backend/app/"
cp "$source_dir/backend/requirements.txt" "$release_dir/backend/"
rsync -a "$stage/frontend/dist/" "$release_dir/frontend/dist/"
cp "$source_dir/scripts/deploy-native.sh" "$release_dir/scripts/"
cp "$source_dir/deploy/systemd/budmon.service" "$release_dir/deploy/systemd/"
cp "$source_dir/deploy/nginx/budmon.budwk.com.conf" "$release_dir/deploy/nginx/"
rsync -a --exclude=.DS_Store "$source_dir/website/dist/" "$release_dir/website/dist/"
cp "$source_dir/deploy/budmon.env.example" "$release_dir/deploy/"
cp "$source_dir/docs/IOS.md" "$release_dir/docs/"
cp "$source_dir/README.md" "$source_dir/LICENSE" "$release_dir/"
cp "$source_dir/VERSION" "$release_dir/VERSION"
# Avoid macOS AppleDouble metadata in archives extracted on Linux.
COPYFILE_DISABLE=1 tar -czf "$stage/$name.tar.gz" -C "$stage" "$name"
mkdir -p "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
mv -f "$stage/$name.tar.gz" "$output_dir/$name.tar.gz"
printf 'Package: %s/%s.tar.gz\n' "$output_dir" "$name"
printf 'On server: sudo mkdir -p /data && sudo tar -xzf budmon.tar.gz -C /data && cd /data/budmon && sudo bash scripts/deploy-native.sh\n'
