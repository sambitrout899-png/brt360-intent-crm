param(
    [string]$Server = "root@187.127.132.149"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundle = Join-Path ([System.IO.Path]::GetTempPath()) "lead-products-$stamp.tar.gz"
$remoteBundle = "/tmp/lead-products-$stamp.tar.gz"
$files = @(
    "backend/app/intent_engine/intent_registry.py",
    "backend/app/orchestration/actions_prospect.py",
    "frontend/src/components/intent/IntentForm.tsx",
    "frontend/src/components/intent/FieldInput.tsx"
)

tar -czf $bundle -C $projectRoot @files
if ($LASTEXITCODE -ne 0) { throw "Packaging failed" }
scp $bundle "${Server}:$remoteBundle"
if ($LASTEXITCODE -ne 0) { throw "Upload failed" }

$remoteScript = @'
set -Eeuo pipefail
app=/opt/brt360
cd "$app"
backup="$app/.deploy_backups/lead-products-__STAMP__"
mkdir -p "$backup"
files=(
  backend/app/intent_engine/intent_registry.py
  backend/app/orchestration/actions_prospect.py
  frontend/src/components/intent/IntentForm.tsx
  frontend/src/components/intent/FieldInput.tsx
  frontend/src/api/client.ts
)
test -f frontend/dist/index.html
systemctl is-active --quiet brt360-api
tar -czf "$backup/source.tar.gz" "${files[@]}"
cp -a frontend/dist "$backup/dist"

rollback() {
  trap - ERR
  set +e
  echo "Deployment failed. Restoring previous source and UI..."
  cd "$app"
  tar -xzf "$backup/source.tar.gz" -C "$app"
  cp -a "$backup/dist/." "$app/frontend/dist/"
  systemctl restart brt360-api
  echo "Restore attempted. Backup: $backup"
  echo "Check service: systemctl status brt360-api"
  exit 1
}
trap rollback ERR
tar -xzf /tmp/lead-products-__STAMP__.tar.gz -C "$app"

# Older installations lack these small helpers used by FieldInput.
# Add only missing exports; preserve the server's existing API client.
"$app/backend/.venv/bin/python" - <<'PY'
from pathlib import Path
import re

path = Path("frontend/src/api/client.ts")
source = path.read_text(encoding="utf-8")
additions = []
if not re.search(r"export\s+(?:interface|type)\s+NotOfferedProduct\b", source):
    additions.append('export interface NotOfferedProduct { name: string; reason: string; fix: "publish" | "segment" | "rule" }')
if not re.search(r"export\s+(?:(?:async\s+)?function|const)\s+notOfferedProducts\b", source):
    additions.append('''export function notOfferedProducts(partyId?: number, segment?: string): Promise<NotOfferedProduct[]> {
  const p = new URLSearchParams();
  if (partyId) p.set("party_id", String(partyId));
  if (segment) p.set("segment", segment);
  return request(`/api/catalog/not-offered?${p.toString()}`);
}''')
if additions:
    path.write_text(source + "\n" + "\n".join(additions) + "\n", encoding="utf-8")
PY

cd "$app/frontend"
export VITE_API_BASE=""
npx tsc -b
npx vite build --outDir "$backup/new-dist"

systemctl restart brt360-api
healthy=0
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
test "$healthy" = 1

# Retain old hashed assets for browsers with an older page open.
cp -a "$backup/new-dist/assets/." "$app/frontend/dist/assets/"
cp "$backup/new-dist/index.html" "$app/frontend/dist/index.html.next"
chmod -R a+rX "$app/frontend/dist"
mv -f "$app/frontend/dist/index.html.next" "$app/frontend/dist/index.html"
trap - ERR
echo "Lead product selection deployed successfully."
echo "Backup: $backup"
'@

$remoteScript.Replace("__STAMP__", $stamp).Replace("`r", "") | ssh $Server "bash -s"
if ($LASTEXITCODE -ne 0) { throw "Deployment failed. Review the output above." }
Write-Host "Deployment complete. Refresh your browser with Ctrl+Shift+R."
