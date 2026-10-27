"""Called by deploy_inventory_sync.sh from backend, with the API stopped."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

# Resolve the same settings as Alembic without displaying credentials.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import get_settings
from sqlalchemy.engine import make_url

backup = Path(os.environ["INVENTORY_DEPLOY_BACKUP"])
url = make_url(get_settings().database_url)
if url.get_backend_name() == "sqlite":
    source = Path(url.database or "")
    if not source.is_file():
        raise SystemExit("Existing SQLite database not found; refusing an update without a backup.")
    with sqlite3.connect(str(source)) as src, sqlite3.connect(str(backup / "database.sqlite")) as dst:
        src.backup(dst)
elif url.get_backend_name() == "postgresql":
    env = dict(os.environ)
    env.update(PGHOST=url.host or "localhost", PGPORT=str(url.port or 5432),
               PGUSER=url.username or "", PGPASSWORD=url.password or "", PGDATABASE=url.database or "")
    for key, value in url.query.items():
        if key in ("sslmode", "sslrootcert", "sslcert", "sslkey"):
            env["PG" + key.upper()] = value
    subprocess.run(["pg_dump", "--format=custom", "--file", str(backup / "database.dump")], env=env, check=True)
else:
    raise SystemExit("Unsupported database: take a verified backup and deploy manually.")
