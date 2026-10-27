"""Training module BRD v1.0: locations, participant directory, certification
catalog, assessment templates and instances, anonymous responses, attendance
log, QA reviews, outcome results, notification templates and log, settings,
append-only audit, one-time codes.

Additive only: new tables, and new nullable columns on the original training
tables. The one relaxation is training_registrations.user_id becoming
nullable (an external participant has no login, SES-007); every existing row
keeps its user.

Revision ID: b2d4f6a8c0e3
Revises: a9c2e5f8b1d4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f6a8c0e3"
down_revision: Union[str, Sequence[str], None] = "a9c2e5f8b1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _system():
    return [
        sa.Column("brt_id", sa.String(length=20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id"),
    ]


def _tenant(nullable=False):
    return sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=nullable)


def upgrade() -> None:
    op.create_table("training_locations",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("code", sa.String(50), nullable=False),
        sa.Column("address", sa.String(1000), nullable=False), sa.Column("timezone", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("tenant_id", "code"), *_system())
    op.create_index("ix_training_locations_tenant_id", "training_locations", ["tenant_id"])

    op.create_table("training_participants",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False), sa.Column("employee_id", sa.String(60), nullable=False),
        sa.Column("organization", sa.String(200), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("training_locations.id"), nullable=True),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("manager", sa.String(200), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("email_invalid", sa.Boolean(), nullable=False), sa.Column("unsubscribed", sa.Boolean(), nullable=False),
        sa.Column("erased_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "email"), *_system())
    op.create_index("ix_training_participants_tenant_id", "training_participants", ["tenant_id"])

    op.create_table("training_certifications",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("validity_months", sa.Integer(), nullable=True), sa.Column("program_ids", sa.JSON(), nullable=False),
        *_system())
    op.create_index("ix_training_certifications_tenant_id", "training_certifications", ["tenant_id"])

    op.create_table("training_templates",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("type", sa.String(20), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version_label", sa.String(20), nullable=False), sa.Column("family_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False), sa.Column("questions", sa.JSON(), nullable=False),
        *_system())
    op.create_index("ix_training_templates_tenant_id", "training_templates", ["tenant_id"])

    op.create_table("training_instances",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("training_sessions.id"), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("training_templates.id"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("opens_at", sa.DateTime(), nullable=True), sa.Column("closes_at", sa.DateTime(), nullable=True),
        sa.Column("anonymous", sa.Boolean(), nullable=False), sa.Column("closed_handled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("session_id", "type"), *_system())
    op.create_index("ix_training_instances_tenant_id", "training_instances", ["tenant_id"])

    # ASM-021: deliberately no reference to a person, registration or invitation
    op.create_table("training_anonymous_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False), _tenant(),
        sa.Column("instance_id", sa.Integer(), sa.ForeignKey("training_instances.id"), nullable=False),
        sa.Column("submitted_hour", sa.DateTime(), nullable=False), sa.Column("answers", sa.JSON(), nullable=False),
        *_system())
    op.create_index("ix_training_anonymous_responses_tenant_id", "training_anonymous_responses", ["tenant_id"])

    op.create_table("training_attendance",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("registration_id", sa.Integer(), sa.ForeignKey("training_registrations.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("method", sa.String(20), nullable=False),
        sa.Column("recorded_by", sa.Integer(), nullable=True), sa.Column("recorded_at", sa.DateTime(), nullable=False),
        *_system())
    op.create_index("ix_training_attendance_tenant_id", "training_attendance", ["tenant_id"])

    op.create_table("training_qa_reviews",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("training_sessions.id"), nullable=False),
        sa.Column("facilitator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("review_date", sa.DateTime(), nullable=False), sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("overall_rating", sa.Float(), nullable=False), sa.Column("comments", sa.String(4000), nullable=False),
        sa.Column("actions", sa.String(4000), nullable=False), *_system())
    op.create_index("ix_training_qa_reviews_tenant_id", "training_qa_reviews", ["tenant_id"])

    op.create_table("training_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("training_sessions.id"), nullable=False),
        sa.Column("participant_key", sa.String(20), nullable=False), sa.Column("objective_code", sa.String(50), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("pre_value", sa.Float(), nullable=True), sa.Column("post_value", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True), sa.Column("normalized_gain", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False), *_system())
    op.create_index("ix_training_outcomes_tenant_id", "training_outcomes", ["tenant_id"])
    op.create_index("ix_training_outcomes_session_id", "training_outcomes", ["session_id"])

    op.create_table("training_notify_templates",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(nullable=True),
        sa.Column("event", sa.String(40), nullable=False), sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False), sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "event", "channel"), *_system())

    op.create_table("training_notifications",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("training_sessions.id"), nullable=True),
        sa.Column("registration_id", sa.Integer(), sa.ForeignKey("training_registrations.id"), nullable=True),
        sa.Column("event", sa.String(40), nullable=False), sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("recipient_participant_id", sa.Integer(), sa.ForeignKey("training_participants.id"), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False), sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("provider_message_id", sa.String(200), nullable=True), sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("subject", sa.String(300), nullable=True), sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True), sa.Column("skip_reason", sa.String(200), nullable=True),
        *_system())
    op.create_index("ix_training_notifications_tenant_id", "training_notifications", ["tenant_id"])
    op.create_index("ix_training_notifications_session_id", "training_notifications", ["session_id"])

    op.create_table("training_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("data", sa.JSON(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), *_system())

    op.create_table("training_audit",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("actor_id", sa.Integer(), nullable=True), sa.Column("action", sa.String(60), nullable=False),
        sa.Column("entity", sa.String(60), nullable=False), sa.Column("entity_id", sa.String(60), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True), sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=False), sa.Column("at", sa.DateTime(), nullable=False), *_system())
    op.create_index("ix_training_audit_tenant_id", "training_audit", ["tenant_id"])

    op.create_table("training_otps",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("registration_id", sa.Integer(), sa.ForeignKey("training_registrations.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), *_system())
    op.create_index("ix_training_otps_tenant_id", "training_otps", ["tenant_id"])

    # ---- new nullable columns on the original tables
    with op.batch_alter_table("training_members") as b:
        b.add_column(sa.Column("roles", sa.JSON(), nullable=True))
        b.add_column(sa.Column("scope_location_ids", sa.JSON(), nullable=True))
        b.add_column(sa.Column("scope_department_ids", sa.JSON(), nullable=True))
        b.add_column(sa.Column("profile", sa.JSON(), nullable=True))
        b.add_column(sa.Column("phone", sa.String(40), nullable=True))
    with op.batch_alter_table("training_programs") as b:
        b.add_column(sa.Column("code", sa.String(50), nullable=True))
        b.add_column(sa.Column("category", sa.String(100), nullable=True))
        b.add_column(sa.Column("audience", sa.String(200), nullable=True))
        b.add_column(sa.Column("delivery_mode", sa.String(20), nullable=True))
        b.add_column(sa.Column("duration_hours", sa.Float(), nullable=True))
        b.add_column(sa.Column("objectives", sa.JSON(), nullable=True))
        b.add_column(sa.Column("status", sa.String(20), nullable=True))
        b.add_column(sa.Column("required_certification_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("training_revisions") as b:
        b.add_column(sa.Column("version_label", sa.String(20), nullable=True))
        b.add_column(sa.Column("state", sa.String(20), nullable=True))
        b.add_column(sa.Column("modules", sa.JSON(), nullable=True))
        b.add_column(sa.Column("objectives", sa.JSON(), nullable=True))
        b.add_column(sa.Column("duration_hours", sa.Float(), nullable=True))
        b.add_column(sa.Column("pre_template_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("post_template_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("feedback_template_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("assessments_waived", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("waive_reason", sa.String(500), nullable=True))
        b.add_column(sa.Column("required_certification_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("retired_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("training_sessions") as b:
        b.add_column(sa.Column("location_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("virtual_url", sa.String(500), nullable=True))
        b.add_column(sa.Column("room", sa.String(100), nullable=True))
        b.add_column(sa.Column("timezone", sa.String(60), nullable=True))
        b.add_column(sa.Column("registration_deadline", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("waitlist_enabled", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("feedback_mode", sa.String(20), nullable=True))
        b.add_column(sa.Column("co_facilitator_ids", sa.JSON(), nullable=True))
        b.add_column(sa.Column("checkin_code", sa.String(40), nullable=True))
        b.add_column(sa.Column("status_reason", sa.String(500), nullable=True))
        b.add_column(sa.Column("override_reason", sa.String(500), nullable=True))
        b.add_column(sa.Column("settings", sa.JSON(), nullable=True))
        b.add_column(sa.Column("notes", sa.Text(), nullable=True))
    op.create_index("uq_training_sessions_checkin_code", "training_sessions", ["checkin_code"], unique=True)
    with op.batch_alter_table("training_registrations") as b:
        b.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("participant_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("status", sa.String(20), nullable=True))
        b.add_column(sa.Column("source", sa.String(20), nullable=True))
        b.add_column(sa.Column("waitlist_position", sa.Integer(), nullable=True))
        b.add_column(sa.Column("registered_at", sa.DateTime(), nullable=True))
    op.create_index("uq_training_registrations_session_participant", "training_registrations", ["session_id", "participant_id"], unique=True)
    with op.batch_alter_table("training_certificates") as b:
        b.add_column(sa.Column("certification_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("issuer", sa.String(200), nullable=True))
        b.add_column(sa.Column("issued_on", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("evidence_url", sa.String(500), nullable=True))
        b.add_column(sa.Column("revoked", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("alerted", sa.JSON(), nullable=True))
    with op.batch_alter_table("training_invitations") as b:
        b.add_column(sa.Column("instance_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("has_submitted", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("sent_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("draft", sa.JSON(), nullable=True))
        b.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("alt_token_hash", sa.String(64), nullable=True))
        b.add_column(sa.Column("consented_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("token_nonce", sa.String(64), nullable=True))
    op.create_index("uq_training_invitations_alt_token_hash", "training_invitations", ["alt_token_hash"], unique=True)
    with op.batch_alter_table("training_responses") as b:
        b.add_column(sa.Column("instance_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("invitation_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("participant_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("confidence_avg", sa.Float(), nullable=True))
        b.add_column(sa.Column("late", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("scored", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("training_responses") as b:
        for c in ("scored", "late", "confidence_avg", "submitted_at", "participant_id", "invitation_id", "instance_id"):
            b.drop_column(c)
    op.drop_index("uq_training_invitations_alt_token_hash", "training_invitations")
    with op.batch_alter_table("training_invitations") as b:
        for c in ("token_nonce", "consented_at", "alt_token_hash", "started_at", "draft", "sent_at", "has_submitted", "instance_id"):
            b.drop_column(c)
    with op.batch_alter_table("training_certificates") as b:
        for c in ("alerted", "revoked", "evidence_url", "issued_on", "issuer", "certification_id"):
            b.drop_column(c)
    op.drop_index("uq_training_registrations_session_participant", "training_registrations")
    with op.batch_alter_table("training_registrations") as b:
        for c in ("registered_at", "waitlist_position", "source", "status", "participant_id"):
            b.drop_column(c)
    op.drop_index("uq_training_sessions_checkin_code", "training_sessions")
    with op.batch_alter_table("training_sessions") as b:
        for c in ("notes", "settings", "override_reason", "status_reason", "checkin_code", "co_facilitator_ids",
                  "feedback_mode", "waitlist_enabled", "registration_deadline", "timezone", "room", "virtual_url", "location_id"):
            b.drop_column(c)
    with op.batch_alter_table("training_revisions") as b:
        for c in ("retired_at", "published_at", "required_certification_id", "waive_reason", "assessments_waived",
                  "feedback_template_id", "post_template_id", "pre_template_id", "duration_hours", "objectives",
                  "modules", "state", "version_label"):
            b.drop_column(c)
    with op.batch_alter_table("training_programs") as b:
        for c in ("required_certification_id", "status", "objectives", "duration_hours", "delivery_mode", "audience", "category", "code"):
            b.drop_column(c)
    with op.batch_alter_table("training_members") as b:
        for c in ("phone", "profile", "scope_department_ids", "scope_location_ids", "roles"):
            b.drop_column(c)
    for t in ("training_otps", "training_audit", "training_settings", "training_notifications", "training_notify_templates",
              "training_outcomes", "training_qa_reviews", "training_attendance", "training_anonymous_responses",
              "training_instances", "training_templates", "training_certifications", "training_participants", "training_locations"):
        op.drop_table(t)
