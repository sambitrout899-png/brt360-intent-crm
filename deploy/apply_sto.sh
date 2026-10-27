#!/usr/bin/env bash
# Deploys the Sales-to-Order / two-kind approvals / B2C release onto an
# existing install (server side). Run as root on the CRM server. The release
# only adds: every existing intent, record, approval rule and pending
# approval keeps working as before.
#
# Usage:  bash apply_sto.sh [bundle] [app_dir]
#   bundle  [/tmp/brt360_full_deploy.tar.gz]   app_dir  [/opt/brt360]
#
# Env options:
#   SKIP_TESTS=1   skip the backend test suite during the build
#   SERVICE        systemd unit of the API [brt360-api]
#   KEEP_BACKUPS   deploy backups kept in .deploy_backups, newest first [5]
#
# Steps (stops at the first failure and prints how to roll back):
#   1. backs up the code (no venv/node_modules/dist) and the database
#   2. stops the API so nothing writes while the database migrates
#   3. extracts the bundle over app_dir (nothing is deleted)
#   4. makes sure backend/.env has a BRT_ID_SECRET (generated once, never changed -
#      every record's BRT_ID is minted with it)
#   5. deploy/brt360.sh build: deps, alembic upgrade head (-> e2a4c6e8f0b3 -> f3b5d7e9a1c4
#      -> a4c6e8f0b2d5 BRT_ID on every table -> c6e8f0a2b4d7 Page Designer / BIM
#      -> e7a9c1b3d5f8 product hierarchies + rules -> f2c4e6a8b0d1 configurator -> a3c5e7f9b1d2 product instances -> b4d6f8a0c2e4 field service -> c5e7a9b1d3f6 order management -> d6f8b0c2e4a7 communications
#      -> f8b0d2e4a6c9 department assignment -> a9c2e5f8b1d4 modular training
#      -> b2d4f6a8c0e3 training BRD -> c3e5a7b9d1f4 B2C direct order), seed, tests, frontend build
#   5. deploy/brt360.sh restart + checks
#
# The migrations add tables/columns/stages and per-tenant reference data
# (process-role positions, pricelists, pricing attributes, the standard B2B/B2C
# prospect and lead fields) where missing, and give every existing row its
# BRT_ID - no row is deleted or changed otherwise. No approval rule is
# switched on and no approval step is re-bound.
set -euo pipefail

BUNDLE="$(readlink -f "${1:-/tmp/brt360_full_deploy.tar.gz}")"
APP_DIR="$(readlink -f "${2:-/opt/brt360}")"
SERVICE="${SERVICE:-brt360-api}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$APP_DIR/.deploy_backups/sto-$STAMP"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$BUNDLE" ]] || die "Bundle not found: $BUNDLE"
[[ -d "$APP_DIR/backend" && -d "$APP_DIR/frontend" ]] || die "$APP_DIR has no backend/ + frontend/"
# Once the server runs on Docker, this script would restart the old systemd API
# on the stale SQLite data - deploy_docker.ps1 is the way in then.
[[ ! -f "$APP_DIR/deploy/compose/.cutover-done" ]] || die "This server runs BRT 360 on Docker now - deploy with deploy/deploy_docker.ps1"
# (read the whole listing: grep -q stops early, and under pipefail tar's broken pipe would count as a failure)
BUNDLE_FILES="$(tar -tzf "$BUNDLE")"
for migration in f3b5d7e9a1c4_product_configuration a4c6e8f0b2d5_brt_id_everywhere c6e8f0a2b4d7_pages_bim_new_connect e7a9c1b3d5f8_product_hierarchy_rules f2c4e6a8b0d1_configurator_engine a3c5e7f9b1d2_product_instances b4d6f8a0c2e4_field_service c5e7a9b1d3f6_order_management d6f8b0c2e4a7_communications f8b0d2e4a6c9_department_assignment a9c2e5f8b1d4_modular_training b2d4f6a8c0e3_training_brd c3e5a7b9d1f4_b2c_direct_order; do
  grep -q "backend/migrations/versions/$migration.py" <<<"$BUNDLE_FILES" \
    || die "$BUNDLE is not this release (migration $migration missing)"
done

failed() {
  printf '\n\033[31mDEPLOY FAILED at line %s.\033[0m The API may be stopped.\n' "$1" >&2
  echo "Roll back with:  bash $BACKUP/rollback_sto.sh" >&2
}

# ---------- 1. backups ----------
step "Backing up code -> $BACKUP/code.tar.gz"
mkdir -p "$BACKUP"
tar -czf "$BACKUP/code.tar.gz" -C "$APP_DIR" \
  --exclude ./.deploy_backups --exclude ./docs/deliverables --exclude '*/.venv' --exclude '*/node_modules' --exclude '*/dist' \
  --exclude '*/__pycache__' --exclude '*.db' --exclude '*.db-journal' .

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" && -f "$APP_DIR/backend/.env" ]]; then
  DB_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/backend/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"'')"
fi
DB_URL="${DB_URL:-sqlite:///./brt360.db}"
DB_FILE=""

step "Stopping $SERVICE (so nothing writes during the backup + migration)"
systemctl stop "$SERVICE" || true
trap 'failed $LINENO' ERR

step "Backing up the database"
case "$DB_URL" in
  sqlite:///*)
    DB_FILE="${DB_URL#sqlite:///}"
    [[ "$DB_FILE" = /* ]] || DB_FILE="$APP_DIR/backend/$DB_FILE"
    [[ -f "$DB_FILE" ]] || die "No SQLite database at $DB_FILE"
    cp -p "$DB_FILE" "$BACKUP/$(basename "$DB_FILE")"
    echo "copied $DB_FILE" ;;
  postgres*)
    command -v pg_dump >/dev/null || die "pg_dump not on PATH - install postgresql-client first"
    pg_dump --format=custom --file="$BACKUP/db.dump" "$(echo "$DB_URL" | sed -E 's#^postgresql\+[a-z0-9]+://#postgresql://#')"
    echo "pg_dump -> $BACKUP/db.dump" ;;
  *) die "Unrecognised DATABASE_URL scheme - back the database up yourself and rerun" ;;
esac

# Rollback script, written next to the backup so it knows exactly what to restore.
cat > "$BACKUP/rollback_sto.sh" <<EOF
#!/usr/bin/env bash
# Restores the code and database saved at $STAMP, then restarts the app.
set -euo pipefail
systemctl stop "$SERVICE" || true
while IFS= read -r f; do rm -f "$APP_DIR/\$f"; done < "$BACKUP/new_files.txt"
tar -xzf "$BACKUP/code.tar.gz" -C "$APP_DIR"
if [[ -n "$DB_FILE" ]]; then
  cp -p "$BACKUP/$(basename "${DB_FILE:-x}")" "$DB_FILE"
else
  pg_restore --clean --if-exists -d "$(echo "$DB_URL" | sed -E 's#^postgresql\+[a-z0-9]+://#postgresql://#')" "$BACKUP/db.dump"
fi
cd "$APP_DIR/frontend" && VITE_API_BASE="" npm run build
bash "$APP_DIR/deploy/brt360.sh" restart
echo "Rolled back to the code + database of $STAMP"
EOF
chmod 700 "$BACKUP/rollback_sto.sh"
chmod 700 "$BACKUP"

# ---------- 2. extract ----------
step "Extracting $BUNDLE -> $APP_DIR"
: > "$BACKUP/new_files.txt"   # files this release adds - rollback deletes them
while IFS= read -r f; do
  f="${f#./}"; [[ -z "$f" || "$f" == */ || -e "$APP_DIR/$f" ]] || echo "$f" >> "$BACKUP/new_files.txt"
done < <(tar -tzf "$BUNDLE")
tar -xzf "$BUNDLE" -C "$APP_DIR"
# Files a release renamed or dropped (REMOVE_FILES, space-separated, relative
# to APP_DIR): extraction only adds, so a stale copy would linger - e.g. a
# renamed migration leaving two files with one revision id. They are in
# code.tar.gz above, so the rollback brings them back.
for f in ${REMOVE_FILES:-}; do
  [[ "$f" != /* && "$f" != *..* ]] || die "REMOVE_FILES must be relative paths inside $APP_DIR: $f"
  if [[ -e "$APP_DIR/$f" ]]; then rm -f "$APP_DIR/$f"; echo "removed obsolete $f"; fi
done

# ---------- 3. the BRT_ID secret ----------
# Every BRT_ID is minted and verified with this key. It is created once and
# must never change afterwards (existing BRT_IDs would stop verifying).
ENV_FILE="$APP_DIR/backend/.env"
if ! grep -qE '^BRT_ID_SECRET=.+' "$ENV_FILE" 2>/dev/null; then
  step "Creating BRT_ID_SECRET in $ENV_FILE (keep it - never change it)"
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || openssl rand -hex 32)"
  [[ -n "$secret" ]] || die "Could not generate BRT_ID_SECRET"
  touch "$ENV_FILE"
  [[ -z "$(tail -c1 "$ENV_FILE")" ]] || echo >> "$ENV_FILE"
  echo "BRT_ID_SECRET=$secret" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  cp -p "$ENV_FILE" "$BACKUP/env.with_brt_id_secret"
else
  echo "BRT_ID_SECRET already set - kept as is"
fi

# ---------- 3b. the token signing key (RS256) ----------
# Login tokens are signed with this private key; other services verify them
# with its public half (/api/auth/jwks). Sessions signed before it stay valid
# until they expire (JWT_ACCEPT_HS256). uid 10001 is the Docker image's user.
JWT_KEY="$APP_DIR/backend/jwt_private.pem"
if ! grep -qE '^JWT_PRIVATE_KEY_FILE=.+' "$ENV_FILE" 2>/dev/null; then
  step "Creating the token signing key $JWT_KEY"
  [[ -f "$JWT_KEY" ]] || openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$JWT_KEY"
  chown 10001 "$JWT_KEY" && chmod 600 "$JWT_KEY"
  [[ -z "$(tail -c1 "$ENV_FILE")" ]] || echo >> "$ENV_FILE"
  echo "JWT_PRIVATE_KEY_FILE=$JWT_KEY" >> "$ENV_FILE"
else
  echo "JWT_PRIVATE_KEY_FILE already set - kept as is"
fi

# ---------- 4. build (deps, migrate, seed, tests, UI) ----------
step "Build"
SKIP_TESTS="${SKIP_TESTS:-0}" bash "$APP_DIR/deploy/brt360.sh" build

# Resolve the expected revision from the code just shipped, not a stale
# release constant. get_current_head also rejects ambiguous migration heads.
TARGET_REVISION="$(cd "$APP_DIR/backend" && .venv/bin/python -c 'from alembic.config import Config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())')"
[[ -n "$TARGET_REVISION" && "$TARGET_REVISION" != "None" ]] || die "Shipped code has no migration head"
current="$(cd "$APP_DIR/backend" && .venv/bin/python -m alembic current 2>/dev/null | awk '{print $1}' | tail -1)"
[[ "$current" == "$TARGET_REVISION" ]] || die "Database is at '$current', expected $TARGET_REVISION"

# ---------- 5. restart ----------
bash "$APP_DIR/deploy/brt360.sh" restart
trap - ERR

# ---------- 6. prune old backups ----------
# Every deploy adds a backup (code + database) and nothing removed them - 150
# had piled up to 42 GB. Only after a good deploy: the newest stays, this one.
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"
step "Keeping the newest $KEEP_BACKUPS deploy backups"
ls -1dt "$APP_DIR"/.deploy_backups/*/ | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -rf
du -sh "$APP_DIR/.deploy_backups"

step "Release deployed (Sales-to-Order, BRT_ID, Page Designer, BIM, New Connect)"
cat <<EOF
Database revision: $current
Backup:            $BACKUP
Roll back:         bash $BACKUP/rollback_sto.sh

Everything that worked before works the same. To use the new process (as a tenant admin):
  Admin -> Org Structure:  put people in Account Manager, Presales, Sales Ops Head, CPMO and
                           Installation team (BSD) - records are handed to them along the way.
  Admin -> Approvals:      your existing rules are listed; pick a position for each step to make
                           it "position holders only" (until then anyone allowed decides it, as
                           before), and switch on ready-made rules (CPMO, lead qualification,
                           consumer discount) if you want them.
  Admin -> Pricelists:     Standard (default, list price), Enterprise -5%, Strategic -10%.
  Admin -> Page Designer:  the B2B / B2C prospect and lead pages - mark fields mandatory, add your
                           own, or tell the AI; rules are checked on every save.
  Admin -> Product Modeller: product hierarchies (root / child products / attributes), Excel/CSV
                           import + export, eligibility / compatibility / constraint rules, slots and
                           classes; Configure & Release: sandbox, model tests, QA, released versions.
                           A product sells from its workspace until its first release.
  Admin -> Integration (BIM): reserve BRT_IDs and load data (CSV / JSON / EAI API) - nothing
                           loads without its BRT_ID.
Every record now has a BRT_ID. Keep BRT_ID_SECRET in backend/.env safe and unchanged
(a copy is in $BACKUP/env.with_brt_id_secret when it was created by this deploy).
EOF
