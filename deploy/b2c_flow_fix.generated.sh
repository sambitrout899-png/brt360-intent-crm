#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR='/opt/brt360'
SERVICE='brt360-api'
API_PORT='8000'
[[ $(id -u) -eq 0 ]] || { echo 'Run this script as root.' >&2; exit 1; }
TARGET="$APP_DIR/backend/app/orchestration/flow_engine.py"
PY="$APP_DIR/backend/.venv/bin/python"
[[ -f "$TARGET" && -x "$PY" ]] || { echo 'Backend file or virtualenv missing.' >&2; exit 1; }
command -v curl >/dev/null
command -v flock >/dev/null
exec 9>"$APP_DIR/.b2c-flow-deploy.lock"
flock -n 9 || { echo 'Another B2C flow deployment is running.' >&2; exit 1; }
systemctl is-active --quiet "$SERVICE" || { echo 'API is not active; resolve its existing failure first.' >&2; exit 1; }
BACKUP=$(mktemp -d "$APP_DIR/.b2c-flow-backup-XXXXXXXX")
cp -p "$TARGET" "$BACKUP/flow_engine.py"
echo "Backup: $BACKUP/flow_engine.py"
changed=0
rollback() {
    rc=$?
    trap - ERR
    if [[ "$changed" == 1 ]]; then
        echo 'Deployment failed; restoring the previous file.' >&2
        cp -p "$BACKUP/flow_engine.py" "$TARGET"
        systemctl restart "$SERVICE" || echo 'Recovery restart failed; inspect systemd logs.' >&2
    fi
    echo "Inspect: journalctl -u $SERVICE -n 80 --no-pager" >&2
    exit "$rc"
}
trap rollback ERR
changed=1
"$PY" - "$TARGET" <<'PY'
import ast
import pathlib
import sys
import textwrap

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
old = '''    missing = [f for f in _required_fields(db, tenant_id, code)[0] if " or " not in f and entities.get(f) in (None, "", [], {})]'''
new = '''    required = _required_fields(db, tenant_id, code)[0]
    # INT-07 also places a consumer order directly, without a quote.
    # Validate that branch's inputs before invoking the same action builder.
    if code == "INT-07" and not entities.get("quote_id"):
        required = [f for f in required if f != "quote_id"] + ["party_id", "offering_name"]
    missing = [f for f in required if " or " not in f and entities.get(f) in (None, "", [], {})]'''
if new in source and old not in source:
    print("Validation fix already installed; checking syntax and restarting.")
elif source.count(old) == 1:
    source = source.replace(old, new, 1)
else:
    raise SystemExit("Server code differs from the expected version. No patch applied.")
ast.parse(source, filename=str(path))
# Verify both supported input paths and the missing-product error before writing.
block = compile(textwrap.dedent(new), "validation", "exec")
for code, entities, expected in [
    ("INT-07", {"party_id": 1, "offering_name": "Example"}, []),
    ("INT-07", {"quote_id": 1}, []),
    ("INT-07", {"party_id": 1}, ["offering_name"]),
    ("INT-07", {}, ["party_id", "offering_name"]),
    ("INT-24", {}, ["quote_id"]),
]:
    scope = {"code": code, "entities": entities, "db": None, "tenant_id": 1,
             "_required_fields": lambda *args: (["quote_id"], None)}
    exec(block, scope)
    assert scope["missing"] == expected, (code, scope["missing"], expected)
path.write_text(source, encoding="utf-8")
print("Syntax and five validation checks passed.")
PY
systemctl restart "$SERVICE"
healthy=0
for attempt in {1..30}; do
    if systemctl is-active --quiet "$SERVICE" && curl --max-time 3 -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null; then
        healthy=1
        break
    fi
    sleep 2
done
[[ "$healthy" == 1 ]] || { echo 'API health check failed.' >&2; false; }
trap - ERR
echo 'B2C Flow Studio validation fix deployed; API health check passed.'
echo "Previous file retained at $BACKUP/flow_engine.py"
