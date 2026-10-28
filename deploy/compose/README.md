# BRT 360 on Docker - single-server runbook

Moves the running install (systemd + SQLite, `deploy/brt360.sh`) onto Docker
Compose + Postgres on the same server, in three stages. Only stage C touches
users, and it can be rolled back.

| Stage | What | Users affected |
|---|---|---|
| A | Install Docker, build the images | No |
| B | Rehearse: copy of the live data into Postgres, full stack on a side port | No |
| C | Cutover: final copy, host nginx switched to the containers | ~30 min maintenance |

The host nginx stays the front door (it also serves the server's other sites).
It passes BRT 360's traffic to the `web` container on `127.0.0.1:8081`.

```
host nginx :80/:443 -> web (nginx + UI) -> api (gunicorn) -> postgres
                                           worker (background jobs, one copy)
                                           host Ollama :11434 (local LLM)
```

All commands run as root on the server, from `/opt/brt360/deploy/compose`
unless they say otherwise. First ship this code with `deploy_fast.ps1` as usual.
That deploy also creates the token signing key `backend/jwt_private.pem`, which
the containers mount. Check that it exists before stage A:
`ls -l /opt/brt360/backend/jwt_private.pem`.

---

## A. Docker and the images

1. Install Docker Engine and the Compose plugin from Docker's own repository
   (not the snap):

   ```bash
   apt-get update && apt-get install -y ca-certificates curl
   install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   chmod a+r /etc/apt/keyrings/docker.asc
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
   apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   ```

2. Cap container logs, so they can't fill the disk:

   ```bash
   echo '{"log-driver": "local", "log-opts": {"max-size": "20m", "max-file": "5"}}' > /etc/docker/daemon.json
   systemctl restart docker && docker compose version
   ```

3. Settings for the stack (the app's own settings stay in `backend/.env`):

   ```bash
   cd /opt/brt360/deploy/compose
   cp .env.example .env && chmod 600 .env
   sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
   sed -i "s/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)/" .env
   mkdir -p backups
   ```

4. Build the images (a few minutes the first time):

   ```bash
   docker compose build
   ```

### Local LLM

The containers reach the host's Ollama at `host.docker.internal:11434`. Ollama
listens on `127.0.0.1` only, so let it listen on all interfaces. The Hostinger
firewall has no rule for 11434, so it stays closed to the internet. Check that
no rule opens it.

```bash
sed -i 's/^Environment="OLLAMA_HOST=.*/Environment="OLLAMA_HOST=0.0.0.0:11434"/' /etc/systemd/system/ollama.service.d/brt360.conf
systemctl daemon-reload && systemctl restart ollama
```

---

## B. Rehearsal (no user impact)

1. A consistent copy of the live database, taken while the app keeps running:

   ```bash
   /opt/brt360/backend/.venv/bin/python -c "import sqlite3; sqlite3.connect('/opt/brt360/backend/brt360.db').backup(sqlite3.connect('backups/rehearsal.db'))"
   chmod 644 backups/rehearsal.db
   ```

2. Postgres, then the schema. This is the first time the migrations run on Postgres,
   so **this is the step most likely to fail**. Keep its output.

   ```bash
   docker compose up -d postgres
   docker compose run --rm migrate
   ```

3. Copy the data. The script checks the schema revision first and compares
   every table's row count at the end:

   ```bash
   docker compose run --rm --no-deps -v ./backups/rehearsal.db:/data/brt360.db:ro api \
     python scripts/sqlite_to_postgres.py sqlite:////data/brt360.db --yes
   ```

4. Start the API and the UI, **but not the worker**. The worker would run
   inventory sync and send training reminders from the copied data.

   ```bash
   docker compose up -d redis
   docker compose up -d --no-deps api web
   curl -s http://127.0.0.1:8081/api/health
   ```

5. Try it from your PC through an SSH tunnel (nothing is opened to the internet):

   ```powershell
   ssh -L 8081:127.0.0.1:8081 root@187.127.132.149
   ```

   Then browse <http://localhost:8081>, log in with your usual account, and check
   customers, orders, Product Modeller and Flow Studio. Look, don't change:
   actions here can still email people or call integrations.

6. Clean up when satisfied (the Postgres volume stays for stage C):

   ```bash
   docker compose stop api web
   ```

---

## C. Cutover (~30 min maintenance)

1. In the Hostinger panel: **Snapshot & backups -> create snapshot**.

2. Stop the old API. The site is down from here on.

   ```bash
   systemctl stop brt360-api
   ```

3. The final copy:

   ```bash
   /opt/brt360/backend/.venv/bin/python -c "import sqlite3; sqlite3.connect('/opt/brt360/backend/brt360.db').backup(sqlite3.connect('backups/final.db'))"
   chmod 644 backups/final.db
   docker compose run --rm migrate
   docker compose run --rm --no-deps -v ./backups/final.db:/data/brt360.db:ro api \
     python scripts/sqlite_to_postgres.py sqlite:////data/brt360.db --yes
   docker compose up -d
   docker compose ps          # postgres, api, worker, web: running / healthy
   ```

4. Point the host nginx at the containers. This keeps the HTTPS certificate from
   "HTTPS" below, which must be done first. Keep the old site file for rollback:

   ```bash
   cp /etc/nginx/sites-available/brt360 /etc/nginx/sites-available/brt360.systemd
   cat > /etc/nginx/sites-available/brt360 <<'EOF'
   server {
       listen 80;
       server_name brt360-tech.com www.brt360-tech.com;
       return 301 https://brt360-tech.com$request_uri;
   }
   server {
       listen 443 ssl http2;
       server_name brt360-tech.com www.brt360-tech.com;
       ssl_certificate /etc/letsencrypt/live/brt360-tech.com/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/brt360-tech.com/privkey.pem;
       include /etc/letsencrypt/options-ssl-nginx.conf;
       ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
       add_header Strict-Transport-Security "max-age=31536000" always;
       client_max_body_size 20m;
       location / {
           proxy_pass http://127.0.0.1:8081;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_read_timeout 120s;
       }
   }
   EOF
   nginx -t && systemctl reload nginx
   ```

5. Smoke test at <https://brt360-tech.com>: log in, open a customer, run one
   intent, open an order. Then keep the old service from starting at boot and
   mark the server as running on Docker. From now on `deploy_fast.ps1` refuses
   to run, and `deploy_docker.ps1` takes over:

   ```bash
   systemctl disable brt360-api
   touch /opt/brt360/deploy/compose/.cutover-done
   ```

### Rollback (during or right after the window)

The SQLite file hasn't changed since step 2. Anything entered after the cutover
exists only in Postgres and is lost on rollback, so decide within the window.

```bash
cd /opt/brt360/deploy/compose && docker compose stop
rm -f .cutover-done
cp /etc/nginx/sites-available/brt360.systemd /etc/nginx/sites-available/brt360
nginx -t && systemctl reload nginx
systemctl enable --now brt360-api
```

---

## D. After the cutover

### Monitoring

```bash
docker compose --profile monitoring up -d
```

Open Grafana through a tunnel (`ssh -L 3000:127.0.0.1:3000 root@187.127.132.149`),
then go to <http://localhost:3000>. Log in as `admin` with `GRAFANA_ADMIN_PASSWORD`
from `.env`. The **BRT 360 / BRT 360 - API** dashboard shows uptime, error rate,
p95 response time, traffic, the slowest routes and the routes failing with 5xx
errors. It uses about 750 MB of RAM in total. Logs: `docker compose logs -f api worker`.

### HTTPS - do this first, before stage A

`brt360-tech.com` and `www.brt360-tech.com` already point at 187.127.132.149.
HTTPS goes on the host nginx, so it works for the current systemd install now
and carries over to Docker at the cutover (step C4 above keeps the certificate).

1. Check which sites nginx serves. This server hosts other projects, so look
   before changing anything:

   ```bash
   grep -RnE "listen|server_name|default_server" /etc/nginx/sites-enabled/
   ```

2. Give the BRT 360 site its domain, then get the certificate. certbot adds the
   443 server and the http -> https redirect, and renews the certificate by itself:

   ```bash
   cp /etc/nginx/sites-available/brt360 /etc/nginx/sites-available/brt360.pre-https
   sed -i 's/server_name _;/server_name brt360-tech.com www.brt360-tech.com;/' /etc/nginx/sites-available/brt360
   nginx -t && systemctl reload nginx
   apt-get install -y certbot python3-certbot-nginx
   certbot --nginx -d brt360-tech.com -d www.brt360-tech.com --redirect --agree-tos -m YOUR-EMAIL -n
   certbot renew --dry-run
   ```

3. Tell the app its public address (CORS, and the links in training QR codes),
   then restart it:

   ```bash
   cd /opt/brt360/backend
   sed -i 's#^CORS_ALLOWED_ORIGINS=.*#CORS_ALLOWED_ORIGINS=https://brt360-tech.com,https://www.brt360-tech.com#' .env
   grep -q '^TRAINING_PUBLIC_ORIGIN=' .env && sed -i 's#^TRAINING_PUBLIC_ORIGIN=.*#TRAINING_PUBLIC_ORIGIN=https://brt360-tech.com#' .env \
     || echo 'TRAINING_PUBLIC_ORIGIN=https://brt360-tech.com' >> .env
   systemctl restart brt360-api        # after the cutover: cd ../deploy/compose && docker compose up -d
   ```

4. Check <https://brt360-tech.com> (padlock, login works), and that
   <http://brt360-tech.com> redirects to it. From now on, use the domain rather
   than the IP address. A request to the bare IP gets no certificate.

Undo: `cp /etc/nginx/sites-available/brt360.pre-https /etc/nginx/sites-available/brt360 && nginx -t && systemctl reload nginx`.

### Tenant isolation in the database (row-level security)

Every table with a `tenant_id` carries the policy `tenant_isolation`. Inside a
signed-in user's request, Postgres shows and accepts only that tenant's rows.
The app connects as the restricted role `brt360_app` (`DATABASE_APP_ROLE`),
because Postgres never applies the policies to a superuser. The `migrate`
service reapplies the policies after every migration (`python -m app.rls`),
so new tables are covered automatically.

```bash
# check: one policy per tenant table
docker compose exec -T postgres psql -U brt360 -d brt360 -tAc "select count(*) from pg_policies where policyname = 'tenant_isolation'"
# switch it off (the rollback) - the app keeps working exactly as before the policies
docker compose run --rm --no-deps api python -m app.rls --off
# switch it on again
docker compose run --rm --no-deps api python -m app.rls
```

### Token signing key rotation (yearly, or if the key may have leaked)

```bash
cd /opt/brt360/backend
openssl pkey -in jwt_private.pem -pubout -out jwt_previous.pub.pem
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out jwt_private.pem.new
mv jwt_private.pem.new jwt_private.pem && chown 10001 jwt_private.pem jwt_previous.pub.pem && chmod 600 jwt_private.pem
```

Mount `jwt_previous.pub.pem` in `docker-compose.yml` next to the private key, set
`JWT_PREVIOUS_PUBLIC_KEY_FILES` to it, and run `docker compose up -d`. Sessions
signed with the old key keep working until they expire (8 hours). After that,
remove both lines. Once every session from before RS256 has expired, set
`JWT_ACCEPT_HS256=false` in `backend/.env`.

---

## Day to day

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Logs | `docker compose logs -f api worker` |
| Deploy new code | from your PC: `deploy\deploy_docker.ps1`. It takes a database dump and a code backup, rebuilds, runs migrations first, then waits for health (`deploy/compose/apply.sh`) |
| Database backup | `docker compose exec postgres pg_dump -U brt360 -Fc brt360 > backups/brt360-$(date +%F).dump` |
| psql shell | `docker compose exec postgres psql -U brt360 brt360` |

Before the cutover, keep deploying with `deploy_fast.ps1`. During stages A and B
the systemd install is still the live one. The CI pipeline (Phase 0) will later
replace both scripts.
