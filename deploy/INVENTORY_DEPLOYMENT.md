# Inventory sync deployment (existing Linux systemd/nginx installation)

Package the full current source on Windows using the existing packager:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\package_full.ps1
scp .\brt360_full_deploy.tar.gz user@server:/tmp/
```

The full bundle is required because this update builds on the current intent catalog and migrations. Local .env files, databases and virtualenvs are excluded. Replace `/opt/brt360` and `user@server` with your existing installation and SSH account.

On the server, preserve the existing code before extracting. Stop the API before replacing its source:

```bash
sudo install -d -m 700 /var/backups/brt360
sudo systemctl stop brt360-api
sudo tar --exclude='.venv' --exclude='node_modules' --exclude='.deploy_backups' \
  -czf /var/backups/brt360/before-inventory-$(date -u +%Y%m%dT%H%M%SZ).tar.gz \
  -C /opt/brt360 backend frontend deploy
sudo tar -xzf /tmp/brt360_full_deploy.tar.gz -C /opt/brt360
sudo bash /opt/brt360/deploy/deploy_inventory_sync.sh /opt/brt360
```

The code backup contains backend/.env and is stored in the root-only backup directory. The update script builds the UI, runs targeted tests, backs up SQLite or PostgreSQL, applies migrations, starts the API and verifies health before publishing the UI. PostgreSQL requires pg_dump. Node must satisfy frontend/package.json dependencies. The existing Python virtualenv and systemd service are required. Allow a maintenance window; failure stops deployment and requires operator recovery.

For a custom service or port:

```bash
sudo env SERVICE=my-api API_PORT=8001 bash /opt/brt360/deploy/deploy_inventory_sync.sh /opt/brt360
```

For real inventory, copy the variables from deploy/inventory.env.example into backend/.env, substitute the numeric tenant ID, HTTPS endpoint and token, then restart the API. The script ensures systemd reads this file. No real API is needed to use **Admin → Feasibility Checks → Load sample check types**. Enable periodic refresh and save the schedule after configuring a real connection. See docs/inventory-check-types.md for the JSON contract.

Database and previous UI backups are saved under .deploy_backups/inventory-TIMESTAMP. If recovery is needed, stop the API, restore the matching code backup and database backup together, then restart. Do not blindly run Alembic downgrade: this release merges two existing migration branches. This script does not automate rollback or overwrite a database after failure.

For a fresh server, use the existing deploy/brt360.sh installation workflow instead.
