#!/usr/bin/env bash
# Run after extracting the current full deployment bundle into APP_DIR.
# Usage: sudo bash deploy/deploy_inventory_sync.sh /opt/brt360
set -euo pipefail
APP_DIR="$(cd "${1:?Usage: deploy_inventory_sync.sh APP_DIR}" && pwd)"
SERVICE="${SERVICE:-brt360-api}"
API_PORT="${API_PORT:-8000}"
PY="$APP_DIR/backend/.venv/bin/python"
[[ -x "$PY" && -f "$APP_DIR/backend/.env" ]] || { echo "Existing backend virtualenv and .env required." >&2; exit 1; }
[[ "$SERVICE" =~ ^[a-zA-Z0-9_.@-]+$ ]] || exit 1
command -v npm >/dev/null
command -v curl >/dev/null
command -v systemctl >/dev/null
SUDO=(); [[ $EUID -eq 0 ]] || SUDO=(sudo)
"${SUDO[@]}" systemctl cat "$SERVICE" >/dev/null
BACKUP="$APP_DIR/.deploy_backups/inventory-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
export INVENTORY_DEPLOY_BACKUP="$BACKUP"

# Prepare the UI before stopping the API; nginx continues serving the old dist.
cd "$APP_DIR/frontend"
npm ci --no-audit --no-fund
VITE_API_BASE="" npm run build -- --outDir dist-inventory-next
cd "$APP_DIR/backend"
"$PY" -m pip install -r requirements.txt
"$PY" -m pytest tests/test_inventory_sync.py tests/test_intent_catalog.py -q -p no:cacheprovider

# A failed migration intentionally leaves the API stopped for operator recovery.
"${SUDO[@]}" systemctl stop "$SERVICE"
trap 'echo "Deployment failed. Backup: $BACKUP. Check migrations before restarting $SERVICE." >&2' ERR
"$PY" "$APP_DIR/deploy/inventory_database_backup.py"
"$PY" -m alembic current > "$BACKUP/alembic-before.txt"
"$PY" -m alembic upgrade head
"$PY" -m alembic current

# The existing systemd service must read backend/.env, as brt360.sh installs it.
# Force that contract for installations with a custom unit too.
"${SUDO[@]}" mkdir -p "/etc/systemd/system/$SERVICE.service.d"
printf '[Service]\nEnvironmentFile="%s/backend/.env"\n' "$APP_DIR" | "${SUDO[@]}" tee "/etc/systemd/system/$SERVICE.service.d/inventory-env.conf" >/dev/null
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl start "$SERVICE"
healthy=0
for attempt in {1..30}; do
  if curl --max-time 3 -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null; then healthy=1; break; fi
  sleep 1
done
[[ "$healthy" == 1 ]] || { echo "API health check failed; inspect journalctl -u $SERVICE" >&2; exit 1; }
if [[ -d "$APP_DIR/frontend/dist" ]]; then mv "$APP_DIR/frontend/dist" "$BACKUP/frontend-dist"; fi
mv "$APP_DIR/frontend/dist-inventory-next" "$APP_DIR/frontend/dist"
chmod -R a+rX "$APP_DIR/frontend/dist"
trap - ERR
printf 'Deployment complete. Database backup: %s\nOpen Admin > Feasibility Checks > Load sample check types.\n' "$BACKUP"
