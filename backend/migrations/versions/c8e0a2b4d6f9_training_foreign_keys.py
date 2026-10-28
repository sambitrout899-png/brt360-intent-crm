"""The training tables' missing foreign keys.

The models declare these links, but no migration created them, so the database
never checked them (alembic check lists them). On Postgres each is added NOT
VALID - every new write is checked from now on - then validated against the
rows already there; a link that existing (test) data breaks is reported and
left unvalidated rather than failing the deploy. SQLite doesn't enforce
foreign keys by default and is left as it is."""
from alembic import op
import sqlalchemy as sa

revision = "c8e0a2b4d6f9"
down_revision = "b5d7f9a1c3e2"
branch_labels = depends_on = None

LINKS = [  # table, column, referenced table
    ("training_certificates", "certification_id", "training_certifications"),
    ("training_invitations", "instance_id", "training_instances"),
    ("training_programs", "required_certification_id", "training_certifications"),
    ("training_registrations", "participant_id", "training_participants"),
    ("training_responses", "instance_id", "training_instances"),
    ("training_responses", "invitation_id", "training_invitations"),
    ("training_responses", "participant_id", "training_participants"),
    ("training_revisions", "feedback_template_id", "training_templates"),
    ("training_revisions", "post_template_id", "training_templates"),
    ("training_revisions", "pre_template_id", "training_templates"),
    ("training_revisions", "required_certification_id", "training_certifications"),
    ("training_sessions", "location_id", "training_locations"),
]


def _name(table: str, column: str) -> str:
    return f"fk_{table}_{column}"[:63]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    for table, column, target in LINKS:
        existing = inspector.get_foreign_keys(table)
        if any(fk["constrained_columns"] == [column] and fk["referred_table"] == target for fk in existing):
            continue
        name = _name(table, column)
        op.execute(f'ALTER TABLE "{table}" ADD CONSTRAINT {name} FOREIGN KEY ("{column}") REFERENCES "{target}" (id) NOT VALID')
        orphans = bind.execute(sa.text(
            f'SELECT COUNT(*) FROM "{table}" t WHERE t."{column}" IS NOT NULL '
            f'AND NOT EXISTS (SELECT 1 FROM "{target}" r WHERE r.id = t."{column}")')).scalar()
        if orphans:
            print(f"  {table}.{column}: {orphans} row(s) point at a missing {target} row - the link is enforced for new "
                  f"writes; fix those rows, then: ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
        else:
            op.execute(f'ALTER TABLE "{table}" VALIDATE CONSTRAINT {name}')


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, _ in LINKS:
        op.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS {_name(table, column)}')
