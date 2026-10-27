#!/usr/bin/env bash
# Rolls back a Phase 7 deploy made by apply_phase7.sh.
#
# Usage:  bash rollback_phase7.sh <app_dir> <backup_dir>
#   backup_dir = the .deploy_backups/phase7-<timestamp> folder apply printed
#
# 1. alembic downgrade to Phase 6 (2d78011f8fbc) - drops the Phase 7 tables.
#    On Postgres the 'superseded' QuoteStatus enum value cannot be removed
#    (see the migration docstring); it stays but is unused. For a byte-exact
#    restore, use the DB backup in backup_dir instead (instructions below).
# 2. restores every overwritten file and deletes files Phase 7 added.
# 3. rebuilds the frontend.
set -euo pipefail

APP_DIR="$(cd "${1:?usage: rollback_phase7.sh <app_dir> <backup_dir>}" && pwd)"
BACKUP="$(cd "${2:?usage: rollback_phase7.sh <app_dir> <backup_dir>}" && pwd)"
[[ -d "$BACKUP/files" ]] || { echo "Not a phase7 backup dir: $BACKUP" >&2; exit 1; }

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }

cd "$APP_DIR/backend"
if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY="$(command -v python3 || command -v python)"; fi

# Downgrade with the Phase 7 code still in place - the migration file must exist.
step "Database: downgrade to 2d78011f8fbc (Phase 6)"
"$PY" -m alembic downgrade 2d78011f8fbc

step "Restoring files"
cp -a "$BACKUP/files/." "$APP_DIR/"
if [[ -f "$BACKUP/new_files.txt" ]]; then
  while IFS= read -r f; do rm -f "$APP_DIR/$f"; echo "removed $f"; done < "$BACKUP/new_files.txt"
fi

if [[ "${SKIP_FRONTEND:-0}" != "1" ]]; then
  step "Frontend: rebuild"
  cd "$APP_DIR/frontend" && npm run build
fi

step "Rolled back to Phase 6"
cat <<EOF
Restart the API process and republish frontend/dist.
Full DB restore instead of downgrade, if ever needed:
  SQLite:   copy the .db file from $BACKUP over the live one (API stopped)
  Postgres: pg_restore --clean --if-exists -d <url> $BACKUP/db.dump
EOF
