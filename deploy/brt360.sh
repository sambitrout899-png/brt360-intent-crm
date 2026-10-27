#!/usr/bin/env bash
# BRT 360 Intent CRM - complete build & run script for a Linux server
# (Ubuntu/Debian, run as root or with sudo).
#
#   bash deploy/brt360.sh all        # first time: setup + build + install, then it's live on http://<server>/
#   bash deploy/brt360.sh build      # after copying new code: deps, migrate, seed, test, build UI
#   bash deploy/brt360.sh restart    # restart the API (+ reload nginx)
#
# Commands:
#   setup    install system packages if missing: Python >= 3.11 (+venv), Node 20, nginx
#   build    backend venv + pip install, create .env if absent, alembic upgrade head,
#            seed reference data (idempotent), run tests, build the frontend
#   install  write + enable the systemd service (gunicorn on 127.0.0.1:$API_PORT) and an
#            nginx site serving frontend/dist and proxying /api to it; then (re)start both
#   all      setup + build + install
#   restart  restart the API service and reload nginx
#   status   service status + health check
#   logs     follow the API logs
#   dev      run in the foreground without systemd/nginx: API on :$API_PORT, Vite dev server on :5173
#
# Env options (defaults in brackets):
#   APP_DIR [folder above this script]   SERVICE [brt360-api]   API_PORT [8000]
#   HTTP_PORT [80]   SERVER_NAME [_ = any host]   WORKERS [2]   SKIP_TESTS [0]
#   PREBUILT_UI [0] - 1 = the bundle carries frontend/dist already built (deploy_fast.ps1): no npm on the server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${APP_DIR:-$SCRIPT_DIR/..}" && pwd)"
BACKEND="$APP_DIR/backend"
FRONTEND="$APP_DIR/frontend"
SERVICE="${SERVICE:-brt360-api}"
API_PORT="${API_PORT:-8000}"
HTTP_PORT="${HTTP_PORT:-80}"
SERVER_NAME="${SERVER_NAME:-_}"
WORKERS="${WORKERS:-2}"
VENV_PY="$BACKEND/.venv/bin/python"
NGINX_SITE="/etc/nginx/sites-available/brt360"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARNING: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"

[[ -d "$BACKEND" && -d "$FRONTEND" ]] || die "$APP_DIR has no backend/ + frontend/ (set APP_DIR)"

# First python >= 3.11 on PATH.
find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

node_ok() { command -v node >/dev/null && [[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]]; }

# ---------------------------------------------------------------- setup
cmd_setup() {
  step "System packages"
  command -v apt-get >/dev/null || die "setup supports apt-based systems only - install Python 3.11+, Node 20+ and nginx yourself, then run: build"
  $SUDO apt-get update -y
  $SUDO apt-get install -y ca-certificates curl gnupg nginx

  if ! find_python >/dev/null; then
    step "Python 3.11+ not found - installing python3.12"
    if ! $SUDO apt-get install -y python3.12 python3.12-venv 2>/dev/null; then
      $SUDO apt-get install -y software-properties-common
      $SUDO add-apt-repository -y ppa:deadsnakes/ppa
      $SUDO apt-get update -y
      $SUDO apt-get install -y python3.12 python3.12-venv
    fi
  fi
  local py; py="$(find_python)" || die "Python 3.11+ still not available"
  # the matching -venv package (Debian/Ubuntu split ensurepip out)
  "$py" -c 'import ensurepip' 2>/dev/null || $SUDO apt-get install -y "$(basename "$py")-venv" || $SUDO apt-get install -y python3-venv
  echo "python: $py ($("$py" --version))"

  if ! node_ok; then
    step "Node 20+ not found - installing Node 20 (NodeSource)"
    curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO bash -
    $SUDO apt-get install -y nodejs
  fi
  echo "node: $(node --version), npm: $(npm --version)"
}

# ---------------------------------------------------------------- build
ensure_env() {
  local env_file="$BACKEND/.env"
  if [[ -f "$env_file" ]]; then
    echo ".env exists - leaving it as is"
    grep -q '^JWT_SECRET_KEY=dev-only' "$env_file" && warn "JWT_SECRET_KEY in .env is still the insecure dev default - change it"
    grep -q 'change-me-now' "$env_file" && warn ".env still has change-me-now bootstrap passwords - change them after first login"
    return 0
  fi
  step "Creating backend/.env (first run)"
  cp "$BACKEND/.env.example" "$env_file"
  local secret host
  secret="$("$VENV_PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  host="$(hostname -I 2>/dev/null | awk '{print $1}')"
  sed -i "s#^JWT_SECRET_KEY=.*#JWT_SECRET_KEY=$secret#" "$env_file"
  sed -i "s#^DATABASE_URL=.*#DATABASE_URL=sqlite:///$BACKEND/brt360.db#" "$env_file"
  # The UI is served same-origin through nginx; these cover direct + dev access.
  sed -i "s#^CORS_ALLOWED_ORIGINS=.*#CORS_ALLOWED_ORIGINS=http://localhost:5173,http://${host:-localhost},http://${host:-localhost}:5173#" "$env_file"
  chmod 600 "$env_file"
  warn "Edit $env_file: set BOOTSTRAP_*_PASSWORD (and DATABASE_URL for Postgres, OPENAI_API_KEY if wanted) BEFORE the seed runs on a production DB."
  warn "Bootstrap logins default to admin@default.local / change-me-now - change them right after first login."
}

cmd_build() {
  local py; py="$(find_python)" || die "Python 3.11+ not found - run: $0 setup"
  node_ok || die "Node 20+ not found - run: $0 setup"

  step "Backend: virtualenv + dependencies"
  cd "$BACKEND"
  [[ -x "$VENV_PY" ]] || "$py" -m venv .venv
  # dependencies only when requirements.txt changed since the last build
  local req_hash; req_hash="$(sha256sum requirements.txt | cut -d' ' -f1)"
  if [[ "$(cat .venv/.requirements.sha256 2>/dev/null)" != "$req_hash" ]]; then
    "$VENV_PY" -m pip install -q --upgrade pip
    "$VENV_PY" -m pip install -q -r requirements.txt
    echo "$req_hash" > .venv/.requirements.sha256
  else
    echo "requirements.txt unchanged - dependencies already installed"
  fi

  ensure_env

  step "Backend: database migrations"
  "$VENV_PY" -m alembic upgrade head
  "$VENV_PY" -m alembic current

  step "Backend: seed reference data (idempotent)"
  "$VENV_PY" -m app.seed

  if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
    step "Backend: test suite"
    "$VENV_PY" -m pytest -q -p no:cacheprovider
  fi

  cd "$FRONTEND"
  if [[ "${PREBUILT_UI:-0}" == "1" && -f dist/index.html ]]; then
    step "Frontend: using the UI built before shipping (PREBUILT_UI=1)"
    echo "UI: $FRONTEND/dist"
    return
  fi
  step "Frontend: install + build"
  # packages only when package-lock.json changed since the last build
  local lock_hash; lock_hash="$(sha256sum package-lock.json 2>/dev/null | cut -d' ' -f1)"
  if [[ ! -d node_modules || -z "$lock_hash" || "$(cat node_modules/.lock.sha256 2>/dev/null)" != "$lock_hash" ]]; then
    if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
    [[ -n "$lock_hash" ]] && echo "$lock_hash" > node_modules/.lock.sha256
  else
    echo "package-lock.json unchanged - packages already installed"
  fi
  # Empty API base = call /api on the same origin (nginx proxies it).
  VITE_API_BASE="" npm run build
  echo "UI built: $FRONTEND/dist"
}

# ---------------------------------------------------------------- install
cmd_install() {
  [[ -x "$VENV_PY" ]] || die "backend not built - run: $0 build"
  [[ -f "$FRONTEND/dist/index.html" ]] || die "frontend not built - run: $0 build"
  command -v nginx >/dev/null || die "nginx not installed - run: $0 setup"

  step "systemd service: $SERVICE"
  $SUDO tee "/etc/systemd/system/$SERVICE.service" >/dev/null <<EOF
[Unit]
Description=BRT 360 Intent CRM API
After=network.target

[Service]
Type=simple
WorkingDirectory=$BACKEND
EnvironmentFile=$BACKEND/.env
ExecStart=$BACKEND/.venv/bin/gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w $WORKERS -b 127.0.0.1:$API_PORT --timeout 120 --access-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "$SERVICE" >/dev/null
  $SUDO systemctl restart "$SERVICE"

  step "nginx site: $NGINX_SITE"
  [[ -f "$NGINX_SITE" ]] && $SUDO cp "$NGINX_SITE" "$NGINX_SITE.bak.$(date +%Y%m%d-%H%M%S)"
  $SUDO tee "$NGINX_SITE" >/dev/null <<EOF
server {
    listen $HTTP_PORT;
    server_name $SERVER_NAME;

    root $FRONTEND/dist;
    index index.html;
    client_max_body_size 20m;

    location /api/ {
        proxy_pass http://127.0.0.1:$API_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }

    location /docs         { proxy_pass http://127.0.0.1:$API_PORT; }
    location /openapi.json { proxy_pass http://127.0.0.1:$API_PORT; }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF
  $SUDO ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/brt360
  if [[ "$HTTP_PORT" == "80" && -e /etc/nginx/sites-enabled/default ]]; then
    warn "Disabling nginx's default site (it also listens on :80)"
    $SUDO rm -f /etc/nginx/sites-enabled/default
  fi
  # nginx (www-data) must be able to read the built UI.
  local d="$FRONTEND/dist"
  while [[ "$d" != "/" ]]; do $SUDO chmod o+x "$d" 2>/dev/null || true; d="$(dirname "$d")"; done
  $SUDO chmod -R o+r "$FRONTEND/dist"
  $SUDO nginx -t
  $SUDO systemctl enable nginx >/dev/null
  $SUDO systemctl reload nginx 2>/dev/null || $SUDO systemctl restart nginx

  if command -v ufw >/dev/null && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    $SUDO ufw allow "$HTTP_PORT/tcp" >/dev/null && echo "ufw: allowed $HTTP_PORT/tcp"
  fi

  cmd_status
}

# ---------------------------------------------------------------- run helpers
cmd_restart() {
  step "Restarting $SERVICE"
  $SUDO systemctl restart "$SERVICE"
  $SUDO systemctl reload nginx 2>/dev/null || true
  cmd_status
}

cmd_status() {
  step "Status"
  systemctl --no-pager --lines=0 status "$SERVICE" 2>/dev/null | head -3 || true
  local ok=""
  for _ in $(seq 1 15); do
    if curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  if [[ -n "$ok" ]]; then echo "API health: OK ($(curl -fsS "http://127.0.0.1:$API_PORT/api/health"))"
  else warn "API not answering on 127.0.0.1:$API_PORT - check: $0 logs"; fi
  if curl -fsS -o /dev/null "http://127.0.0.1:$HTTP_PORT/" 2>/dev/null; then
    echo "UI: http://$(hostname -I 2>/dev/null | awk '{print $1}')$([[ $HTTP_PORT == 80 ]] || echo ":$HTTP_PORT")/"
  fi
}

cmd_logs() { $SUDO journalctl -u "$SERVICE" -f -n 100; }

cmd_dev() {
  [[ -x "$VENV_PY" ]] || die "backend not built - run: $0 build"
  step "Dev mode: API http://0.0.0.0:$API_PORT  |  UI http://0.0.0.0:5173   (Ctrl+C stops both)"
  cd "$BACKEND"
  "$VENV_PY" -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$API_PORT" &
  local api_pid=$!
  trap 'kill $api_pid 2>/dev/null' EXIT INT TERM
  cd "$FRONTEND"
  VITE_API_BASE="${VITE_API_BASE:-http://$(hostname -I 2>/dev/null | awk '{print $1}'):$API_PORT}" npx vite --host 0.0.0.0
}

case "${1:-}" in
  setup)   cmd_setup ;;
  build)   cmd_build ;;
  install) cmd_install ;;
  all)     cmd_setup; cmd_build; cmd_install ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  dev)     cmd_dev ;;
  *) sed -n '2,30p' "$0"; exit 1 ;;
esac
