#!/usr/bin/env bash
# Deploys a code bundle onto the Docker install (server side) - what
# apply_sto.sh is for the systemd install. deploy/deploy_docker.ps1 calls it.
#
# Usage:  bash apply.sh [bundle] [app_dir]
#   bundle  [/tmp/brt360_docker_deploy.tar.gz]   app_dir  [/opt/brt360]
#
# Env options:
#   KEEP_BACKUPS   code backups and database dumps kept, newest first [5]
#
# Steps (stops at the first failure and prints how to roll back):
#   1. dumps the Postgres database and backs up the code
#   2. extracts the bundle over app_dir (nothing is deleted)
#   3. docker compose up -d --build: images rebuilt, migrations + seed run
#      first (the migrate service), then api / worker / web are replaced
#   4. waits for /api/health through the web container
set -euo pipefail

BUNDLE="$(readlink -f "${1:-/tmp/brt360_docker_deploy.tar.gz}")"
APP_DIR="$(readlink -f "${2:-/opt/brt360}")"
COMPOSE_DIR="$APP_DIR/deploy/compose"
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$APP_DIR/.deploy_backups/docker-$STAMP"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$BUNDLE" ]] || die "Bundle not found: $BUNDLE"
[[ -f "$COMPOSE_DIR/.cutover-done" ]] || die "This server doesn't run BRT 360 on Docker yet (no $COMPOSE_DIR/.cutover-done) - deploy with deploy_fast.ps1, or do the cutover in deploy/compose/README.md first"
[[ -f "$COMPOSE_DIR/.env" ]] || die "$COMPOSE_DIR/.env is missing"
command -v docker >/dev/null || die "docker is not installed"
cd "$COMPOSE_DIR"

# The old systemd API must never run beside the containers: its background jobs
# would work on the stale SQLite data.
if systemctl is-enabled --quiet brt360-api 2>/dev/null || systemctl is-active --quiet brt360-api 2>/dev/null; then
  step "Stopping and disabling the old systemd API (brt360-api)"
  systemctl disable --now brt360-api
fi

# ---------- 1. backups ----------
step "Backing up the database -> $COMPOSE_DIR/backups/pre-deploy-$STAMP.dump"
mkdir -p backups "$BACKUP"
docker compose exec -T postgres pg_dump -U brt360 -Fc brt360 > "backups/pre-deploy-$STAMP.dump"
step "Backing up code -> $BACKUP/code.tar.gz"
tar -czf "$BACKUP/code.tar.gz" -C "$APP_DIR" \
  --exclude ./.deploy_backups --exclude ./deploy/compose/backups --exclude ./docs/deliverables \
  --exclude '*/.venv' --exclude '*/node_modules' \
  --exclude '*/dist' --exclude '*/__pycache__' --exclude '*.db' .
chmod 700 "$BACKUP"

failed() {
  printf '\n\033[31mDEPLOY FAILED at line %s.\033[0m\n' "$1" >&2
  cat >&2 <<EOF
To roll back:
  cd $COMPOSE_DIR && docker compose stop api worker web
  tar -xzf $BACKUP/code.tar.gz -C $APP_DIR
  # only if a migration changed the database:
  docker compose exec -T postgres pg_restore -U brt360 -d brt360 --clean --if-exists < backups/pre-deploy-$STAMP.dump
  docker compose up -d --build
EOF
}
trap 'failed $LINENO' ERR

# ---------- 2. extract ----------
step "Extracting $BUNDLE -> $APP_DIR"
tar -xzf "$BUNDLE" -C "$APP_DIR"

# ---------- 3. build + replace ----------
step "Building images and replacing the containers (migrations run first)"
docker compose up -d --build --remove-orphans

# ---------- 4. health ----------
WEB_PORT="$(grep -E '^WEB_PORT=' .env | cut -d= -f2)"
step "Waiting for http://127.0.0.1:${WEB_PORT:-8081}/api/health"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT:-8081}/api/health" >/dev/null 2>&1; then
    trap - ERR
    docker compose ps
    # prune: the newest KEEP_BACKUPS code backups and database dumps stay
    ls -1dt "$APP_DIR"/.deploy_backups/docker-*/ | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -rf
    ls -1t backups/pre-deploy-*.dump | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
    step "Deployed. Backup: $BACKUP and backups/pre-deploy-$STAMP.dump"
    exit 0
  fi
  sleep 2
done
docker compose logs --tail 50 api >&2
false  # health never came up: the ERR trap prints the rollback
