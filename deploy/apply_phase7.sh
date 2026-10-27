#!/usr/bin/env bash
# Applies the Phase 7 overlay (lifecycle, assignment, custom fields, AI Flow
# Studio, record panel / intent drawer UI) onto an existing Phase 6 install.
#
# Usage:  bash apply_phase7.sh <app_dir> [path/to/phase7_deploy.tar.gz]
#   app_dir = the folder containing backend/ and frontend/
#
# Env options:
#   SKIP_TESTS=1     skip the backend test suite after migrating
#   SKIP_FRONTEND=1  skip npm install + build (e.g. frontend built elsewhere)
#   RESTART_CMD="sudo systemctl restart brt360-api"  run after a successful deploy
#
# What it does, in order (stops at the first failure):
#   1. backs up every file the overlay will overwrite, plus the database
#   2. extracts the overlay over app_dir
#   3. pip install -r requirements.txt, alembic upgrade head (-> 7a1c9e3d5b20)
#   4. runs the backend tests (they use their own throwaway DB)
#   5. npm install + npm run build -> frontend/dist
set -euo pipefail

APP_DIR="$(cd "${1:?usage: apply_phase7.sh <app_dir> [bundle]}" && pwd)"
BUNDLE="$(cd "$(dirname "${2:-phase7_deploy.tar.gz}")" && pwd)/$(basename "${2:-phase7_deploy.tar.gz}")"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$APP_DIR/.deploy_backups/phase7-$STAMP"

[[ -f "$BUNDLE" ]] || { echo "Bundle not found: $BUNDLE" >&2; exit 1; }
[[ -d "$APP_DIR/backend" && -d "$APP_DIR/frontend" ]] || { echo "$APP_DIR has no backend/ + frontend/" >&2; exit 1; }

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }

# ---------- 1. backups ----------
step "Backing up files the overlay replaces -> $BACKUP"
mkdir -p "$BACKUP/files"
tar -tzf "$BUNDLE" | grep -v '/$' > "$BACKUP/overlay_files.txt"
while IFS= read -r f; do
  if [[ -f "$APP_DIR/$f" ]]; then
    mkdir -p "$BACKUP/files/$(dirname "$f")"
    cp -p "$APP_DIR/$f" "$BACKUP/files/$f"
  else
    echo "$f" >> "$BACKUP/new_files.txt"   # rollback deletes these
  fi
done < "$BACKUP/overlay_files.txt"

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" && -f "$APP_DIR/backend/.env" ]]; then
  DB_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/backend/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"'')"
fi
DB_URL="${DB_URL:-sqlite:///./brt360.db}"

step "Backing up the database"
case "$DB_URL" in
  sqlite:///*)
    DB_FILE="${DB_URL#sqlite:///}"
    [[ "$DB_FILE" = /* ]] || DB_FILE="$APP_DIR/backend/$DB_FILE"
    if [[ -f "$DB_FILE" ]]; then cp -p "$DB_FILE" "$BACKUP/$(basename "$DB_FILE")"; echo "copied $DB_FILE"
    else echo "no SQLite file at $DB_FILE yet - nothing to back up"; fi ;;
  postgres*)
    if command -v pg_dump >/dev/null; then
      PG_URL="$(echo "$DB_URL" | sed -E 's#^postgresql\+[a-z0-9]+://#postgresql://#')"
      pg_dump --format=custom --file="$BACKUP/db.dump" "$PG_URL"
      echo "pg_dump -> $BACKUP/db.dump"
    else
      echo "WARNING: pg_dump not on PATH - take a database backup yourself before continuing." >&2
      read -r -p "Continue without a DB backup? [y/N] " ok; [[ "$ok" == [yY] ]] || exit 1
    fi ;;
  *) echo "WARNING: unrecognised DATABASE_URL scheme - back the database up manually." >&2 ;;
esac
echo "$DB_URL" | sed -E 's#://[^@]*@#://***@#' > "$BACKUP/database_url.txt"

# ---------- 2. overlay ----------
step "Extracting overlay into $APP_DIR"
tar -xzf "$BUNDLE" -C "$APP_DIR"

# ---------- 3. backend ----------
step "Backend: dependencies + migration"
cd "$APP_DIR/backend"
if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY="$(command -v python3 || command -v python)"; fi
"$PY" -m pip install -q -r requirements.txt
"$PY" -m alembic upgrade head
"$PY" -m alembic current

# ---------- 4. tests ----------
if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  step "Backend: test suite"
  "$PY" -m pytest -q -p no:cacheprovider
fi

# ---------- 5. frontend ----------
if [[ "${SKIP_FRONTEND:-0}" != "1" ]]; then
  step "Frontend: install + build"
  cd "$APP_DIR/frontend"
  if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
  npm run build
  echo "static build: $APP_DIR/frontend/dist"
fi

if [[ -n "${RESTART_CMD:-}" ]]; then
  step "Restarting: $RESTART_CMD"
  eval "$RESTART_CMD"
fi

step "Phase 7 deployed"
cat <<EOF
Backup:   $BACKUP
Rollback: bash $APP_DIR/deploy/rollback_phase7.sh $APP_DIR $BACKUP
Restart the API process (gunicorn/uvicorn) if you did not set RESTART_CMD,
and publish frontend/dist wherever the UI is served from.
EOF
