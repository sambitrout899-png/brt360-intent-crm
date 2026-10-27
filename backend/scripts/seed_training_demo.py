"""Seed Training & Learning demo data into one organization (BRD 12.5).

    python -m scripts.seed_training_demo --tenant-slug default

Safe to run twice: it does nothing once the demo programs exist. Needs the
organization to be licensed for Training & Learning."""
from app.crm_core.training_demo import main

if __name__ == "__main__":
    main()
