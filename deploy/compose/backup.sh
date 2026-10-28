#!/bin/sh
# Nightly database dump - run by the `backup` service (docker-compose.yml) at
# 02:00 UTC, or by hand:  docker compose exec backup /scripts/backup.sh
#
# Writes backups/nightly-YYYY-MM-DD.dump (pg_dump custom format), checks it
# can be read back, and keeps the newest BACKUP_KEEP_DAYS days of them.
# Restore: see README.md, "Backups".
set -eu

KEEP="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%F)"
OUT="/backups/nightly-$STAMP.dump"

pg_dump -h postgres -U brt360 -d brt360 -Fc -f "$OUT.part"
# a dump that can't be listed back is no backup - keep the last good one instead
pg_restore --list "$OUT.part" > /dev/null
mv "$OUT.part" "$OUT"
find /backups -name 'nightly-*.dump' -mtime "+$KEEP" -delete
# for monitoring: when the last good backup finished (alert BackupMissing after 26 h)
mkdir -p /backups/metrics
printf '# HELP brt360_backup_last_success_timestamp_seconds Last good nightly database dump.\n# TYPE brt360_backup_last_success_timestamp_seconds gauge\nbrt360_backup_last_success_timestamp_seconds %s\n' \
  "$(date -u +%s)" > /backups/metrics/backup.prom.tmp && mv /backups/metrics/backup.prom.tmp /backups/metrics/backup.prom
echo "$(date -u +%FT%TZ) backup ok: $OUT ($(du -h "$OUT" | cut -f1)), kept $(ls /backups/nightly-*.dump | wc -l) nightly dump(s)"
