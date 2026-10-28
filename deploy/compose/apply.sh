#!/usr/bin/env bash
# Deploys a code bundle onto the Docker install (server side) - what
# apply_sto.sh is for the systemd install. deploy/deploy_docker.ps1 calls it.
#
# Usage:  bash apply.sh [bundle] [app_dir]
#   bundle  [/tmp/brt360_docker_deploy.tar.gz]   app_dir  [/opt/brt360]
#
# Env options:
#   KEEP_BACKUPS   code backups and database dumps kept, newest first [5]
#   SERVICE        all [default], or one service to release on its own: training
#                  (+ training-worker), ai-gateway, api (+ worker), web. The others
#                  keep running their current images. Refused when the bundle brings
#                  a new database migration - those need everything deployed together.
#
# Steps (stops at the first failure and prints how to roll back):
#   1. dumps the Postgres database and backs up the code
#   2. extracts the bundle over app_dir (nothing is deleted)
#   3. docker compose up -d --build: images rebuilt, migrations + seed run
#      first (the migrate service), then the containers are replaced - with
#      SERVICE, only that service's
#   4. waits for /api/health through the web container, and for the training
#      and AI gateway containers to report healthy
set -euo pipefail

BUNDLE="$(readlink -f "${1:-/tmp/brt360_docker_deploy.tar.gz}")"
APP_DIR="$(readlink -f "${2:-/opt/brt360}")"
COMPOSE_DIR="$APP_DIR/deploy/compose"
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"
SERVICE="${SERVICE:-all}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$APP_DIR/.deploy_backups/docker-$STAMP"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$BUNDLE" ]] || die "Bundle not found: $BUNDLE"
[[ -f "$COMPOSE_DIR/.cutover-done" ]] || die "This server doesn't run BRT 360 on Docker yet (no $COMPOSE_DIR/.cutover-done) - deploy with deploy_fast.ps1, or do the cutover in deploy/compose/README.md first"
[[ -f "$COMPOSE_DIR/.env" ]] || die "$COMPOSE_DIR/.env is missing"
command -v docker >/dev/null || die "docker is not installed"
cd "$COMPOSE_DIR"

case "$SERVICE" in
  all)        CONTAINERS="" ;;
  training)   CONTAINERS="training training-worker" ;;
  ai-gateway) CONTAINERS="ai-gateway" ;;
  bim|product|metadata) CONTAINERS="$SERVICE" ;;
  api)        CONTAINERS="api worker" ;;
  web)        CONTAINERS="web" ;;
  *) die "SERVICE must be all, training, bim, product, metadata, ai-gateway, api or web (got '$SERVICE')" ;;
esac
# the split services' web containers, which must report healthy after a deploy
HEALTHCHECKED="training bim product metadata ai-gateway"
if [[ -n "$CONTAINERS" ]]; then
  # a migration changes the database every service shares: release everything together then
  new_migrations=""
  while read -r f; do
    [[ -f "$APP_DIR/${f#./}" ]] || new_migrations+="${f##*/} "
  done < <(tar -tzf "$BUNDLE" | grep -E '^\./backend/migrations/versions/[^/]+\.py$' || true)
  [[ -z "$new_migrations" ]] || die "This release has new database migrations (${new_migrations% }) - deploy everything (no -Service)"
fi

# The old systemd API must never run beside the containers: its background jobs
# would work on the stale SQLite data.
if systemctl is-enabled --quiet brt360-api 2>/dev/null || systemctl is-active --quiet brt360-api 2>/dev/null; then
  step "Stopping and disabling the old systemd API (brt360-api)"
  systemctl disable --now brt360-api
fi

# Secrets a release needs, made once per server - before anything runs docker compose,
# which refuses the compose file while one is missing.
if ! grep -qE '^AI_GATEWAY_TOKEN=.+' .env; then
  step "Creating AI_GATEWAY_TOKEN in .env (shared by the app and the AI gateway)"
  sed -i '/^AI_GATEWAY_TOKEN=/d' .env
  echo "AI_GATEWAY_TOKEN=$(openssl rand -hex 24)" >> .env
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
  cd $COMPOSE_DIR && docker compose stop ${CONTAINERS:-api worker web training training-worker bim product metadata ai-gateway}
  tar -xzf $BACKUP/code.tar.gz -C $APP_DIR
  # only if a migration changed the database:
  docker compose exec -T postgres pg_restore -U brt360 -d brt360 --clean --if-exists < backups/pre-deploy-$STAMP.dump
  docker compose up -d --build ${CONTAINERS:+--no-deps $CONTAINERS}
EOF
}
trap 'failed $LINENO' ERR

# ---------- 2. extract ----------
step "Extracting $BUNDLE -> $APP_DIR"
tar -xzf "$BUNDLE" -C "$APP_DIR"

# ---------- 3. build + replace ----------
if [[ -n "$CONTAINERS" ]]; then
  step "Building and replacing only: $CONTAINERS (everything else keeps running as it is)"
  # shellcheck disable=SC2086
  docker compose up -d --build --no-deps $CONTAINERS
else
  step "Building images and replacing the containers (migrations run first)"
  docker compose up -d --build --remove-orphans
fi

# ---------- 4. health ----------
WEB_PORT="$(grep -E '^WEB_PORT=' .env | cut -d= -f2)"
step "Waiting for http://127.0.0.1:${WEB_PORT:-8081}/api/health"
all_healthy() {
  local svc
  for svc in $HEALTHCHECKED; do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null)" == healthy ]] || return 1
  done
}
for _ in $(seq 1 90); do  # containers report healthy after their first check (~30 s)
  if curl -fsS "http://127.0.0.1:${WEB_PORT:-8081}/api/health" >/dev/null 2>&1 && all_healthy; then
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
docker compose ps >&2
# shellcheck disable=SC2086
docker compose logs --tail 30 api $HEALTHCHECKED >&2
false  # health never came up: the ERR trap prints the rollback
