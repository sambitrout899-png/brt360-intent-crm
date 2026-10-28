#!/usr/bin/env bash
# One-time: a staging copy of BRT 360 next to production on this server.
#
#   bash /opt/brt360/deploy/compose/setup_staging.sh staging.brt360-tech.com you@example.com
#     domain  - must already point at this server (a DNS A record)
#     email   - for Let's Encrypt certificate notices
#
# What it makes, sharing nothing with production but the server:
#   /opt/brt360-staging      its own code (copied from production now; later deploys with
#                            deploy_docker.ps1 -Environment staging), database, secrets, signing key
#   127.0.0.1:8082           its web container (compose project brt360-staging)
#   https://<domain>         host nginx: HTTPS, and a password on the pages (the app's own login
#                            protects the API - the browser's password prompt and the app's token
#                            would both need the Authorization header)
# Fresh secrets throughout: staging's data and tokens never mix with production's.
set -euo pipefail

DOMAIN="${1:?usage: setup_staging.sh <domain> <email>}"
EMAIL="${2:?usage: setup_staging.sh <domain> <email>}"
PROD=/opt/brt360
STAGE=/opt/brt360-staging
SITE=/etc/nginx/sites-available/brt360-staging
HTPASSWD=/etc/nginx/brt360-staging.htpasswd

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
secret() { openssl rand -hex "${1:-24}"; }

[[ $EUID -eq 0 ]] || die "run as root"
[[ ! -e "$STAGE" ]] || die "$STAGE exists - staging is set up already; deploy to it with deploy_docker.ps1 -Environment staging"
[[ -f "$PROD/deploy/compose/.cutover-done" ]] || die "production isn't on Docker yet"
ip="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')"
[[ -n "$ip" ]] || die "$DOMAIN doesn't resolve yet - add the DNS A record (-> this server) and wait a few minutes"
hostname -I | tr ' ' '\n' | grep -qx "$ip" || die "$DOMAIN points at $ip, not at this server ($(hostname -I))"

step "Code: copied from production"
mkdir -p "$STAGE"
tar -C "$PROD" -cf - \
  --exclude ./.deploy_backups --exclude ./deploy/compose/backups --exclude ./deploy/compose/.env \
  --exclude ./deploy/compose/.cutover-done --exclude ./deploy/compose/monitoring/alertmanager.yml \
  --exclude ./backend/.env --exclude './backend/*.pem' --exclude './backend/*.db' --exclude ./backend/.venv \
  --exclude ./frontend/node_modules --exclude ./frontend/dist --exclude ./docs/deliverables --exclude ./sermon_output . \
  | tar -C "$STAGE" -xf -

step "Settings and secrets (fresh, staging only)"
ENV="$STAGE/backend/.env"
cp "$STAGE/backend/.env.example" "$ENV"
admin_pw="$(secret 9)"; platform_pw="$(secret 9)"
sed -i \
  -e "s#^JWT_SECRET_KEY=.*#JWT_SECRET_KEY=$(secret 32)#" \
  -e "s#^CORS_ALLOWED_ORIGINS=.*#CORS_ALLOWED_ORIGINS=https://$DOMAIN#" \
  -e "s#^TRAINING_PUBLIC_ORIGIN=.*#TRAINING_PUBLIC_ORIGIN=https://$DOMAIN#" \
  -e "s#^TRAINING_EMAIL_ADAPTER=.*#TRAINING_EMAIL_ADAPTER=outbox#" \
  -e "s#^BOOTSTRAP_TENANT_ADMIN_PASSWORD=.*#BOOTSTRAP_TENANT_ADMIN_PASSWORD=$admin_pw#" \
  -e "s#^BOOTSTRAP_PLATFORM_ADMIN_PASSWORD=.*#BOOTSTRAP_PLATFORM_ADMIN_PASSWORD=$platform_pw#" \
  "$ENV"
echo "BRT_ID_SECRET=$(secret 32)" >> "$ENV"
# the AI key production uses, if any (staging calls the same provider)
if grep -qE '^OPENAI_API_KEY=.+' "$PROD/backend/.env"; then
  sed -i "s#^OPENAI_API_KEY=.*#$(grep -E '^OPENAI_API_KEY=' "$PROD/backend/.env" | tail -1)#" "$ENV"
fi
chmod 600 "$ENV"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$STAGE/backend/jwt_private.pem" 2>/dev/null
chown 10001 "$STAGE/backend/jwt_private.pem" && chmod 600 "$STAGE/backend/jwt_private.pem"

COMPOSE="$STAGE/deploy/compose"
cp "$COMPOSE/.env.staging.example" "$COMPOSE/.env"
sed -i -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(secret 24)/" -e "s/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=$(secret 16)/" \
  -e "s/^AI_GATEWAY_TOKEN=.*/AI_GATEWAY_TOKEN=$(secret 24)/" "$COMPOSE/.env"
chmod 600 "$COMPOSE/.env"
mkdir -p "$COMPOSE/backups"
touch "$COMPOSE/.cutover-done"   # deployed with deploy_docker.ps1 -Environment staging

step "Building and starting the staging stack (a few minutes)"
cd "$COMPOSE"
docker compose up -d --build
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8082/api/health >/dev/null 2>&1 && break
  sleep 3
done
curl -fsS http://127.0.0.1:8082/api/health >/dev/null || die "staging didn't come up - docker compose -p brt360-staging logs --tail 50 api"

step "Host nginx: $DOMAIN, with a password on the pages"
page_pw="$(secret 9)"
printf 'brt360:%s\n' "$(openssl passwd -apr1 "$page_pw")" > "$HTPASSWD"
chown root:www-data "$HTPASSWD" && chmod 640 "$HTPASSWD"
cat > "$SITE" <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 20m;
    add_header X-Robots-Tag "noindex, nofollow" always;
    # the API answers only to the app's own login (its token travels in the Authorization header)
    location /api/ {
        proxy_pass http://127.0.0.1:8082;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
    location / {
        auth_basic "BRT 360 staging";
        auth_basic_user_file $HTPASSWD;
        proxy_pass http://127.0.0.1:8082;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf "$SITE" /etc/nginx/sites-enabled/brt360-staging
nginx -t && systemctl reload nginx
certbot --nginx -d "$DOMAIN" --redirect --agree-tos -m "$EMAIL" -n

step "Staging is ready"
cat <<EOF
  URL:                https://$DOMAIN
  Page password:      brt360 / $page_pw
  Tenant admin:       admin@default.local / $admin_pw
  Platform admin:     platform-admin@brt360.local / $platform_pw
Write these down now - they are not shown again (the passwords are in $ENV and $HTPASSWD).
Deploy to staging:  deploy\\deploy_docker.ps1 -Environment staging
EOF
