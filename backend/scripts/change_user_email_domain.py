"""Ops data fix: move every user of ONE tenant to a new email domain.

    python -m scripts.change_user_email_domain --tenant "Vodafone Idea" --domain Vi.com           # dry run
    python -m scripts.change_user_email_domain --tenant "Vodafone Idea" --domain Vi.com --apply   # write

Run from the backend directory (same DATABASE_URL as the API). The tenant is
matched by slug or exact name. Only users of that tenant change; the part
before @ is kept (asha.mehta@old.com -> asha.mehta@Vi.com). Nothing is
written if any new address would collide with an existing user, or without
--apply. Every change is printed, and the old -> new list is saved to a CSV
next to this script so the change can be reversed.

Users sign in with their email, compared exactly - after this they must use
the new address, typed exactly as stored.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

from app.db import SessionLocal
from app.models.auth import User
from app.models.tenant import Tenant

_DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", required=True, help="tenant slug or exact name")
    parser.add_argument("--domain", required=True, help="new domain, e.g. Vi.com (an @ in front is fine)")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args()

    domain = args.domain.strip().lstrip("@")
    if not _DOMAIN.match(domain):
        print(f"'{domain}' is not a valid domain")
        return 2

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter((Tenant.slug == args.tenant) | (Tenant.name == args.tenant)).first()
        if tenant is None:
            names = ", ".join(f"{t.name} [{t.slug}]" for t in db.query(Tenant).order_by(Tenant.name))
            print(f"No tenant '{args.tenant}'. Tenants: {names}")
            return 2

        users = db.query(User).filter(User.tenant_id == tenant.id).order_by(User.id).all()
        changes = []
        for user in users:
            local = user.email.rsplit("@", 1)[0]
            new_email = f"{local}@{domain}"
            if new_email != user.email:
                changes.append((user, user.email, new_email))

        print(f"Tenant: {tenant.name} [{tenant.slug}] - {len(users)} user(s), {len(changes)} to change")
        for user, old, new in changes:
            print(f"  #{user.id:<5} {old:<45} -> {new}")

        # Collisions: two users mapping to one address, or onto someone else's.
        new_emails = [new for _, _, new in changes]
        duplicates = {e for e in new_emails if new_emails.count(e) > 1}
        changing_ids = {u.id for u, _, _ in changes}
        taken = {
            u.email
            for u in db.query(User).filter(User.email.in_(new_emails)).all()
            if u.id not in changing_ids
        } if new_emails else set()
        if duplicates or taken:
            for e in sorted(duplicates):
                print(f"  CONFLICT: several users would become {e}")
            for e in sorted(taken):
                print(f"  CONFLICT: {e} already belongs to another user")
            print("Nothing written.")
            return 1

        if not changes:
            print("Nothing to do.")
            return 0
        if not args.apply:
            print("Dry run - nothing written. Re-run with --apply to make the change.")
            return 0

        backup = Path(__file__).with_name(f"email_domain_{tenant.slug}_{dt.datetime.now():%Y%m%d_%H%M%S}.csv")
        with backup.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["user_id", "old_email", "new_email"])
            writer.writerows((u.id, old, new) for u, old, new in changes)
        for user, _, new in changes:
            user.email = new
        db.commit()
        print(f"Updated {len(changes)} user(s). Old -> new saved to {backup}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
