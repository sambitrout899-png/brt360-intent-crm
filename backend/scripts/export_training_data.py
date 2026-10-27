"""Export fine-tuning data for the platform's own telecom CRM model.

    python -m scripts.export_training_data --out training.jsonl                    # all tenants + generated examples
    python -m scripts.export_training_data --tenant "Vodafone Idea" --out vi.jsonl # one tenant's history + generated
    python -m scripts.export_training_data --history-only --out real.jsonl          # only what users really ran

Run from the backend directory (same DATABASE_URL as the API). The file is
chat JSONL - feed it to deploy/llm/finetune.py (see deploy/llm/README.md).
It holds what users typed, so treat it like customer data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.crm_core import training_data
from app.db import SessionLocal
from app.models.tenant import Tenant


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="training.jsonl")
    parser.add_argument("--tenant", help="tenant slug or exact name (default: all tenants)")
    parser.add_argument("--history-only", action="store_true", help="skip the generated examples")
    parser.add_argument("--per-template", type=int, default=12, help="generated examples per template (default 12)")
    parser.add_argument("--style", choices=training_data.STYLES, default="reasoned",
                        help="reasoned (default): think-first answers, look-alikes, vague and out-of-scope requests, planning; "
                             "plain: the original examples")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        tenant_id = None
        if args.tenant:
            tenant = db.query(Tenant).filter((Tenant.slug == args.tenant) | (Tenant.name == args.tenant)).first()
            if tenant is None:
                print(f"No tenant '{args.tenant}'")
                return 2
            tenant_id = tenant.id
        real = sum(1 for _ in training_data.history(db, tenant_id))
        out = Path(args.out)
        count = 0
        with out.open("w", encoding="utf-8") as fh:
            for line in training_data.to_jsonl(training_data.build(db, tenant_id, args.per_template, not args.history_only, args.style)):
                fh.write(line)
                count += 1
        print(f"Wrote {count} examples to {out.resolve()} ({real} from real usage, {count - real} generated)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
