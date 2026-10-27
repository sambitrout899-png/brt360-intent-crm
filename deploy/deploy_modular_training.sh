#!/usr/bin/env bash
# Existing Linux/systemd/nginx installation; run the script OUTSIDE the live tree.
# sudo bash deploy_modular_training.sh /opt/brt360 /tmp/brt360_full_deploy.tar.gz https://crm.example.com
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# == 3 ]] || fail "Usage: $0 APP_DIR BUNDLE PUBLIC_FRONTEND_ORIGIN"
[[ $EUID == 0 ]] || fail 'Run with sudo (the existing systemd API service must be stopped).'
APP_DIR="$(cd "$1" && pwd -P)"
BUNDLE="$(realpath "$2")"
PUBLIC_ORIGIN="$3"
SERVICE="${SERVICE:-brt360-api}"
API_PORT="${API_PORT:-8000}"
PY="$APP_DIR/backend/.venv/bin/python"
[[ "$APP_DIR" != / ]] || fail 'APP_DIR cannot be /'
[[ "$SERVICE" =~ ^[a-zA-Z0-9_@.-]+$ ]] || fail 'Invalid service name'
[[ "$API_PORT" =~ ^[0-9]+$ ]] && (( API_PORT >= 1 && API_PORT <= 65535 )) || fail 'Invalid API_PORT'
[[ -f "$BUNDLE" && -x "$PY" && -f "$APP_DIR/backend/.env" ]] || fail 'Bundle, existing virtualenv and backend/.env are required'
for cmd in npm curl tar systemctl flock; do command -v "$cmd" >/dev/null || fail "Missing command: $cmd"; done
systemctl cat "$SERVICE" >/dev/null
exec 9>"$APP_DIR/.modular-deployment.lock"
flock -n 9 || fail 'Another modular deployment is already running'

mkdir -p "$APP_DIR/.deploy_backups"
chmod 700 "$APP_DIR/.deploy_backups"
BACKUP="$(mktemp -d "$APP_DIR/.deploy_backups/modular-training-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 700 "$BACKUP"
STAGE="$BACKUP/staged"
mkdir "$STAGE"
STOPPED=0
on_failure() {
  local code=$?
  trap - ERR
  if [[ "$STOPPED" == 1 ]]; then systemctl stop "$SERVICE" || true; fi
  printf '\nDeployment failed (exit %s). Backup/staging: %s\n' "$code" "$BACKUP" >&2
  printf 'The API is left stopped if maintenance had begun. Restore matching code, environment and database together; do not automatically downgrade.\n' >&2
  exit "$code"
}
trap on_failure ERR
export MODULAR_STAGE="$STAGE" MODULAR_BUNDLE="$BUNDLE" TRAINING_PUBLIC_ORIGIN="$PUBLIC_ORIGIN"

# Reject traversal, links/devices, and production data in the uploaded artifact.
"$PY" - <<'PY'
import os, tarfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit
origin = urlsplit(os.environ['TRAINING_PUBLIC_ORIGIN'])
if origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('', '/'):
    raise SystemExit('PUBLIC_FRONTEND_ORIGIN must be an http(s) origin without credentials, query or path')
with tarfile.open(os.environ['MODULAR_BUNDLE'], 'r:gz') as archive:
    members = []
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or '\\' in member.name:
            raise SystemExit('Unsafe archive path')
        if not path.parts or path.parts[0] not in {'backend', 'frontend', 'deploy', 'docs'}:
            continue
        if not (member.isfile() or member.isdir()):
            raise SystemExit('Archive links and special files are not supported')
        if any(p in {'.env', '.venv', 'node_modules', '.git', '.deploy_backups'} for p in path.parts) or path.suffix in {'.db', '.sqlite'}:
            raise SystemExit('Bundle includes production data or local dependencies; use package_full.ps1')
        members.append(member)
    archive.extractall(os.environ['MODULAR_STAGE'], members=members)
PY
for file in backend/requirements.txt frontend/package-lock.json backend/migrations/versions/a9c2e5f8b1d4_modular_training.py deploy/inventory_database_backup.py; do
  [[ -f "$STAGE/$file" ]] || fail "Release is missing $file"
done

printf '\nBuilding staged frontend...\n'
(cd "$STAGE/frontend"; npm ci --no-audit --no-fund; VITE_API_BASE='' npm run build)
[[ -f "$STAGE/frontend/dist/index.html" ]] || fail 'Frontend build is missing index.html'

printf '\nStarting maintenance; backup: %s\n' "$BACKUP"
systemctl stop "$SERVICE"
STOPPED=1
cd "$APP_DIR/backend"
export INVENTORY_DEPLOY_BACKUP="$BACKUP"
"$PY" "$STAGE/deploy/inventory_database_backup.py"
"$PY" -m alembic current > "$BACKUP/alembic-before.txt"
"$PY" -m pip freeze > "$BACKUP/python-requirements-before.txt"
systemctl cat "$SERVICE" > "$BACKUP/service-before.txt"
# .env is intentionally included in this root-only recovery backup.
tar --exclude='.venv' --exclude='node_modules' --exclude='__pycache__' --exclude='.pytest_cache' \
  -czf "$BACKUP/code-before.tar.gz" -C "$APP_DIR" backend frontend deploy

printf '\nInstalling dependencies and testing staged release...\n'
(umask 022; "$PY" -m pip install -r "$STAGE/backend/requirements.txt")
(cd "$STAGE/backend"; DATABASE_URL='sqlite:///:memory:' "$PY" -m pytest \
  tests/test_module_catalog_visibility.py tests/test_modular_training.py tests/test_tenant_management.py tests/test_tenant_branding.py \
  tests/test_org_access.py tests/test_migrated_schema.py tests/test_assignment_and_pages.py \
  tests/test_intent_catalog.py tests/test_access_guards.py tests/test_inventory_sync.py \
  tests/test_no_garbled_text.py -q -p no:cacheprovider)

# Overlay application source only: do not replace .env, the venv or the live UI yet.
for directory in backend deploy docs; do
  if [[ -d "$STAGE/$directory" ]]; then
    mkdir -p "$APP_DIR/$directory"
    (cd "$STAGE"; tar -cf - --exclude='__pycache__' --exclude='.pytest_cache' "$directory") | tar -xf - -C "$APP_DIR"
  fi
done
(cd "$STAGE"; tar -cf - --exclude='node_modules' --exclude='dist' frontend) | tar -xf - -C "$APP_DIR"
"$PY" - <<'PY'
import os
from dotenv import set_key
metadata = os.stat('.env')
set_key('.env', 'TRAINING_PUBLIC_ORIGIN', os.environ['TRAINING_PUBLIC_ORIGIN'].rstrip('/'))
os.chown('.env', metadata.st_uid, metadata.st_gid)
os.chmod('.env', metadata.st_mode & 0o777)
PY
"$PY" -m alembic upgrade head
"$PY" -m alembic current > "$BACKUP/alembic-after.txt"
"$PY" - <<'PY'
from sqlalchemy import inspect
from app.db import engine
with engine.connect() as connection:
    schema = inspect(connection)
    assert 'training_sessions' in schema.get_table_names(), 'Training migration missing'
    assert 'licensed_modules' in {c['name'] for c in schema.get_columns('tenants')}, 'License migration missing'
PY

# Reuse the existing unit, adding only the documented environment-file contract.
DROPIN="/etc/systemd/system/$SERVICE.service.d"
mkdir -p "$DROPIN"
if [[ -f "$DROPIN/modular-training.conf" ]]; then cp "$DROPIN/modular-training.conf" "$BACKUP/modular-training.conf.before"; fi
printf '[Service]\nEnvironmentFile="%s/backend/.env"\n' "$APP_DIR" > "$DROPIN/modular-training.conf"
# Files copied from root-only staging must be readable by the existing service user.
chmod -R a+rX "$APP_DIR/backend/app" "$APP_DIR/backend/migrations"
systemctl daemon-reload
systemctl start "$SERVICE"
healthy=0
for attempt in {1..30}; do
  if curl --max-time 3 -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null; then healthy=1; break; fi
  sleep 1
done
[[ "$healthy" == 1 ]] || { echo "API health check failed; inspect journalctl -u $SERVICE" >&2; false; }
# Ensure this is the new application, not another process answering the health URL.
curl --max-time 10 -fsS "http://127.0.0.1:$API_PORT/openapi.json" | "$PY" -c \
  'import json,sys; p=json.load(sys.stdin)["paths"]; assert "/api/training/workspace" in p and "/api/modules" in p'

if [[ -e "$APP_DIR/frontend/dist" ]]; then mv "$APP_DIR/frontend/dist" "$BACKUP/frontend-dist-before"; fi
mv "$STAGE/frontend/dist" "$APP_DIR/frontend/dist"
chmod -R a+rX "$APP_DIR/frontend/dist"
STOPPED=0
trap - ERR
printf '\nDeployment complete. Recovery files: %s\n' "$BACKUP"
printf 'Open Platform Admin > Edit tenant > Licensed modules. Existing tenants retain their previous licenses.\n'
printf 'QR origin: %s. nginx must route /assessment/* to frontend/dist/index.html.\n' "$PUBLIC_ORIGIN"
printf 'Invitations/reminders use the in-app inbox; external email/SMS is not configured.\n'
