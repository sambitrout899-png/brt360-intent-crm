# Deploy modular licensing and training (existing Linux server)

This updates the existing systemd/nginx installation, defaulting to `/opt/brt360`,
`brt360-api`, and port 8000. It does not execute a remote deployment automatically.
Allow a maintenance window. Stop any other application instances/workers that
write to the same database before running it.

## Single command from Windows PowerShell

Replace the server login and public URL, then run this entire command on one line:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\BRT 360\brt360-intent-crm\deploy\deploy_modular_training.ps1" -Server "user@YOUR_SERVER" -PublicOrigin "https://YOUR_DOMAIN"
```

This packages, uploads and runs the Linux deployment automatically using Windows
OpenSSH (`ssh.exe` and `scp.exe`). SSH may prompt for host verification/password;
non-root users need sudo access. Optional parameters: `-AppDir /opt/brt360`,
`-Service brt360-api`, `-ApiPort 8000`, `-SshPort 22`, and `-IdentityFile <key-path>`.
Use `-PackageOnly` without server parameters to package without connecting.
Uploaded releases are retained in a unique `/tmp/brt360-modular-*` directory.

## Manual alternative: 1. Package and upload from Windows

Run from the project directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\package_full.ps1 -SkipChecks
scp .\brt360_full_deploy.tar.gz user@server:/tmp/
scp .\deploy\deploy_modular_training.sh user@server:/tmp/
```

`-SkipChecks` skips the packager's full test suite; the deployment script builds
the UI and runs the focused regression suite itself before migration. The bundle
excludes the local database, virtualenv, dependencies and `.env`.

## 2. Deploy on the server

Replace the example origin with the URL users open in their browser:

```bash
sudo bash /tmp/deploy_modular_training.sh \
  /opt/brt360 /tmp/brt360_full_deploy.tar.gz https://crm.example.com
```

For a different existing service or API port:

```bash
sudo env SERVICE=my-api API_PORT=8001 bash /tmp/deploy_modular_training.sh \
  /opt/brt360 /tmp/brt360_full_deploy.tar.gz https://crm.example.com
```

Requires the existing `backend/.env` and Linux virtualenv, npm/Node compatible
with the project's lockfile, systemd, curl, tar, flock and package-registry access.
PostgreSQL also requires `pg_dump`. Existing database credentials and JWT secrets
are preserved. The public origin is written to `TRAINING_PUBLIC_ORIGIN` in `.env`.

The script validates and stages the archive, builds the frontend, stops the API,
backs up SQLite/PostgreSQL and existing application files, installs dependencies,
runs tests, overlays source, applies **all pending migrations**, starts the API,
checks health and new API routes, then publishes the built UI. It does not seed or
reset the database, change existing module selections, or replace nginx settings.
The standard nginx configuration already supports `/assessment/*` through its
SPA fallback. Verify that fallback if using a custom nginx configuration.

## Recovery

The script prints a root-only `.deploy_backups/modular-training-TIMESTAMP-*` directory under the
application directory. It contains `database.sqlite` or `database.dump`,
`code-before.tar.gz` (including the old `.env`), Python requirements, prior service
configuration and migration revisions. Retain it securely: it contains secrets
and business data. Staging is retained for diagnosis.

A failure after maintenance starts leaves the API stopped. Inspect the failure
and `journalctl -u brt360-api`. To roll back, stop all writers and restore the
matching old application files, `.env`, dependency environment, systemd drop-in,
and database together. PostgreSQL restoration requires the deployment's normal
DB administrator procedure. Do not blindly run Alembic downgrade or restore a
database while writers are active. The script does not perform automatic rollback.

After deployment, activate Training & Learning in Platform Admin. Configure users
and training roles, then create the first program. Email/SMS delivery remains
unconfigured; automated invitations and reminders appear in the in-app inbox.
