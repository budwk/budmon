#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

usage() {
    cat <<'HELP'
Usage: sudo bash scripts/deploy-native.sh [--skip-apt]
Deploy an extracted native package on Ubuntu with Python 3.12–3.14 and systemd.
Build the archive locally first with scripts/package-native.sh.
Requires an existing Nginx installation loading /etc/nginx/conf.d/*.conf.
  --skip-apt  Use already installed system packages (no apt update/install).
  --help      Show this help.
HTTPS: budmon.budwk.com (DNS must point here; ports 80/443 must be reachable).
First deployment: Certbot asks for account details and agreement acceptance.
Optional: PYTHON_BIN=/usr/bin/python3.14 (default: python3)
Venv: ~/.budmon-venv (sudo caller home); code: /data/budmon.
Data: /var/lib/budmon; config: /data/budmon/.env.
Existing config/data are preserved. Re-running upgrades code and restarts services.
HELP
}
fail() { echo "Error: $*" >&2; exit 1; }
skip_apt=0
for arg in "$@"; do
    case "$arg" in
        --skip-apt) skip_apt=1 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; fail "Unknown argument: $arg" ;;
    esac
done
[[ $(uname -s) == Linux ]] || fail 'This script requires Ubuntu Linux.'
[[ $EUID -eq 0 ]] || fail 'Run this script with sudo.'
[[ -d /run/systemd/system ]] || fail 'systemd must be running as PID 1.'
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Validate the extracted package before installing packages or stopping services.
for file in website/dist/index.html backend/app/certs/AppleRootCA-G3.cer frontend/dist/index.html backend/app/main.py backend/requirements.txt \
    deploy/systemd/budmon.service deploy/budmon.env.example; do
    [[ -f "$source_dir/$file" ]] || fail "Missing $file; upload and extract the archive from scripts/package-native.sh first."
done
[[ -f "$source_dir/deploy/nginx/budmon.budwk.com.conf" || -f "$source_dir/deploy/nginx/budmon.conf" ]] || fail "Missing deploy/nginx/budmon.budwk.com.conf; upload and extract the archive from scripts/package-native.sh first."
command -v nginx >/dev/null || fail 'Nginx must already be installed.'
[[ -d /etc/nginx/conf.d ]] || fail 'Expected /etc/nginx/conf.d.'
python_bin=${PYTHON_BIN:-python3}
if (( ! skip_apt )); then
    apt-get update
    apt-get install -y python3 python3-venv rsync curl ca-certificates tzdata certbot python3-certbot-nginx postgresql postgresql-contrib
fi
systemctl enable postgresql.service
systemctl start postgresql.service
for cmd in "$python_bin" rsync curl nginx systemctl runuser certbot; do
    command -v "$cmd" >/dev/null || fail "Missing command: $cmd"
done
"$python_bin" -c 'import sys; assert (3,12) <= sys.version_info < (3,15), "Python 3.12–3.14 required"'
deploy_user=${SUDO_USER:-$(id -un)}
deploy_home=$("$python_bin" -c 'import pwd, sys; print(pwd.getpwnam(sys.argv[1]).pw_dir)' "$deploy_user")
[[ "$deploy_home" == /* && "$deploy_home" != *[!a-zA-Z0-9_./-]* ]] || fail 'Unsupported home directory path.'
venv_dir="$deploy_home/.budmon-venv"

# Temporary workspace for service rendering and import checks.
stage=$(mktemp -d /tmp/budmon-deploy.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
trap 'echo "Deployment failed; inspect the output and journalctl -u budmon. Fix the error and rerun the script." >&2' ERR
if ! id budmon >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/budmon --no-create-home --shell /usr/sbin/nologin budmon
fi
install -d -m 0755 /data/budmon /data/budmon/backend /data/budmon/frontend/dist /data/budmon/website/dist
install -d -m 0750 /var/lib/budmon
env_file="/data/budmon/.env"
if [[ ! -e "$env_file" ]]; then
    if [[ -f /etc/budmon/budmon.env ]]; then
        cp -p /etc/budmon/budmon.env "$env_file"
    else
        install -m 0640 "$source_dir/deploy/budmon.env.example" "$env_file"
    fi
fi
if ! grep -q '^BUDMON_DATABASE_URL=' "$env_file"; then
    echo "BUDMON_DATABASE_URL=postgresql://budmon:budmon@127.0.0.1:5432/budmon" >> "$env_file"
fi
chmod 0640 "$env_file" 2>/dev/null || true

# Synchronize PostgreSQL role and database with the configured credentials
db_user="budmon"
db_pass="budmon"
db_name="budmon"
db_host="127.0.0.1"
db_port="5432"

target_env="$env_file"
[[ -f "$target_env" ]] || target_env="/etc/budmon/budmon.env"

if [[ -f "$target_env" ]]; then
    env_pass=$(grep -E '^[[:space:]]*(BUDMON_DB_PASSWORD|POSTGRES_PASSWORD)=' "$target_env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r"' "'")
    if [[ -n "$env_pass" ]]; then
        db_pass="$env_pass"
    else
        env_url=$(grep -E '^[[:space:]]*(BUDMON_DATABASE_URL|DATABASE_URL)=' "$target_env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r"' "'")
        if [[ "$env_url" =~ ://([^:]+):(.*)@([^:/]+)(:([0-9]+))?/([^?]+) ]]; then
            db_user="${BASH_REMATCH[1]}"
            db_pass="${BASH_REMATCH[2]}"
            db_host="${BASH_REMATCH[3]}"
            if [[ -n "${BASH_REMATCH[5]}" ]]; then
                db_port="${BASH_REMATCH[5]}"
            fi
            db_name="${BASH_REMATCH[6]}"
        fi
    fi
fi

if command -v psql >/dev/null; then
    sql_pass="${db_pass//\'/\'\'}"
    sudo -u postgres psql -c "DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$db_user') THEN
            CREATE ROLE $db_user WITH LOGIN PASSWORD '$sql_pass';
        ELSE
            ALTER ROLE $db_user WITH PASSWORD '$sql_pass';
        END IF;
    END
    \$\$;"
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$db_name'" 2>/dev/null | grep -q 1; then
        sudo -u postgres psql -c "CREATE DATABASE $db_name OWNER $db_user;"
    else
        sudo -u postgres psql -c "ALTER DATABASE $db_name OWNER TO $db_user;" 2>/dev/null || true
    fi
    sudo -u postgres psql -d "$db_name" -c "GRANT ALL ON SCHEMA public TO $db_user;" 2>/dev/null || true
fi
# Stop before modifying the installed environment/code; do not run two schedulers.
if systemctl is-active --quiet budmon.service; then
    systemctl stop budmon.service
fi
if [[ -d "$venv_dir" ]]; then
    "$venv_dir/bin/python" -c 'import sys; print(sys.version_info[:2])' > "$stage/venv-version"
    "$python_bin" -c 'import sys; print(sys.version_info[:2])' > "$stage/python-version"
    cmp -s "$stage/venv-version" "$stage/python-version" || fail "Python version changed; move $venv_dir aside and rerun."
else
    "$python_bin" -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install -r "$source_dir/backend/requirements.txt"
"$venv_dir/bin/python" -m pip check
chmod -R a+rX "$venv_dir"
chown -R "$deploy_user:$(id -gn "$deploy_user")" "$venv_dir"
# Source may itself live in /data/budmon; avoid rsyncing a directory onto itself.
if [[ "$source_dir" != /data/budmon ]]; then
    rsync -a --delete --exclude=__pycache__ "$source_dir/backend/app/" /data/budmon/backend/app/
    install -m 0644 "$source_dir/backend/requirements.txt" /data/budmon/backend/requirements.txt
fi
chmod -R a+rX /data/budmon/backend/app
# Check imports as the environment owner; service access is checked after startup.
install -d -o "$deploy_user" -g "$(id -gn "$deploy_user")" -m 0700 "$stage/smoke"
chmod 0755 "$stage"
(
    cd /data/budmon/backend
    runuser -u "$deploy_user" -- env BUDMON_DATA_DIR="$stage/smoke" "$venv_dir/bin/python" -c \
        'from app.main import app; from app.security import hash_password, verify_password; assert verify_password("deploy-check", hash_password("deploy-check")); app.openapi()'
)
if [[ "$source_dir" != /data/budmon ]]; then
    rsync -a --delete "$source_dir/frontend/dist/" /data/budmon/frontend/dist/
    rsync -a --delete "$source_dir/website/dist/" /data/budmon/website/dist/
fi
chmod -R a+rX /data/budmon/frontend/dist /data/budmon/website/dist
cp "$source_dir/deploy/systemd/budmon.service" "$stage/budmon.service"
install -m 0644 "$stage/budmon.service" /etc/systemd/system/budmon.service
# Deploy single Nginx configuration file for budmon.budwk.com.
nginx_config=/etc/nginx/conf.d/budmon.budwk.com.conf
nginx_src="$source_dir/deploy/nginx/budmon.budwk.com.conf"
[[ -f "$nginx_src" ]] || nginx_src="$source_dir/deploy/nginx/budmon.conf"

if [[ -e "$nginx_config" ]]; then
    cp -p "$nginx_config" "$nginx_config.before-budmon-$(date +%Y%m%d%H%M%S).bak"
fi
install -m 0644 "$nginx_src" "$nginx_config"
rm -f /etc/nginx/budmon-routes.conf
# Disable only previous BudMon installer entries; leave their contents for reference.
for old_config in /etc/nginx/conf.d/budmon.ailoly.xyz.conf /etc/nginx/conf.d/budmon.ailol.xyz.conf; do
    if [[ -f "$old_config" ]]; then mv "$old_config" "$old_config.disabled-$(date +%Y%m%d%H%M%S)"; fi
done
if [[ -L /etc/nginx/sites-enabled/budmon ]] && [[ $(readlink -f /etc/nginx/sites-enabled/budmon) == /etc/nginx/sites-available/budmon ]]; then
    unlink /etc/nginx/sites-enabled/budmon
fi
nginx -t
systemctl daemon-reload
systemctl enable budmon.service nginx.service
systemctl restart budmon.service
systemctl start nginx.service
systemctl reload nginx.service
healthy=0
for ((attempt=0; attempt<30; attempt++)); do
    if curl --fail --silent --max-time 2 http://127.0.0.1:9977/api/health >/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done
if (( ! healthy )); then
    journalctl -u budmon.service -n 50 --no-pager >&2
    fail 'Backend health check failed.'
fi
systemctl is-active --quiet budmon.service nginx.service
# Certbot configures HTTPS and redirects HTTP; existing valid certificates are reused.
# Keep first-time account/terms prompts interactive for the server administrator.
certbot --nginx --cert-name budmon.budwk.com -d budmon.budwk.com --redirect --keep-until-expiring
systemctl enable --now certbot.timer
nginx -t
systemctl reload nginx.service
curl --fail --silent --show-error --max-time 15 \
    --resolve budmon.budwk.com:443:127.0.0.1 https://budmon.budwk.com/api/health >/dev/null
echo "Python environment: $venv_dir"
cat <<'DONE'
BudMon deployed and enabled at boot.
Website: https://budmon.budwk.com
Admin: https://budmon.budwk.com/admin/
API: https://budmon.budwk.com/api
Application: /data/budmon
Config: /data/budmon/.env (restart budmon after changes)
Data: /var/lib/budmon
Logs: journalctl -u budmon -f
DONE
