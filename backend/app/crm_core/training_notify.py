"""Training workflow automation (BRD section 8): planned messages per
session (WFA-001), reminders only to those who have not completed the step
at send time (WFA-002), templates with checked merge fields (WFA-003),
swappable channel adapters (WFA-004), quiet hours (WFA-005), delivery
tracking (WFA-006), retries with backoff and idempotency (WFA-007), manual
resend (WFA-008), unsubscribe (WFA-009) and the per-session log (WFA-010).

The scheduler (training_service.run_due_training) calls `tick` every minute."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import logging
import os
import re
import smtplib
from email.message import EmailMessage

from fastapi import HTTPException
from sqlalchemy import update

from app.config import get_settings
from app.crm_core import training_policy as policy
from app.models.auth import User, UserRole
from app.models.tenant import Tenant
from app.models.training import (TrainingCertificate, TrainingInstance, TrainingInvitation, TrainingLocation, TrainingMember,
                                 TrainingNotification, TrainingNotifyTemplate, TrainingParticipant, TrainingRegistration,
                                 TrainingSession)
from app.timeutils import utcnow

log = logging.getLogger(__name__)

MERGE_FIELDS = {"participant.first_name", "participant.name", "session.title", "session.start_local", "session.end_local",
                "session.location", "session.join_url", "assessment.link", "feedback.link", "qr.image", "checkin.link",
                "org.name", "unsubscribe.link", "otp.code", "summary.text", "certification.name", "certification.expires"}
FIELD = re.compile(r"{{\s*([^}]*?)\s*}}")

# event -> (subject, text body); HTML is the text with line breaks unless an org overrides it
DEFAULTS: dict[str, tuple[str, str]] = {
    "registration_confirmed": ("You're registered: {{session.title}}",
                               "Hi {{participant.first_name}},\n\nYou're registered for {{session.title}} on {{session.start_local}} at {{session.location}}.\n\n"
                               "Please complete the short pre-session assessment: {{assessment.link}}\n\n{{org.name}}"),
    "pre_reminder_1": ("Reminder: pre-session assessment for {{session.title}}",
                       "Hi {{participant.first_name}},\n\nPlease complete your pre-session assessment before {{session.start_local}}: {{assessment.link}}\n\n"
                       "Stop reminders: {{unsubscribe.link}}"),
    "pre_reminder_2": ("Final reminder: {{session.title}} starts soon",
                       "Hi {{participant.first_name}},\n\nLast chance to complete your pre-session assessment: {{assessment.link}}\n\nStop reminders: {{unsubscribe.link}}"),
    "session_day": ("Today: {{session.title}}",
                    "Hi {{participant.first_name}},\n\n{{session.title}} starts at {{session.start_local}}.\nWhere: {{session.location}} {{session.join_url}}\n"
                    "Check in by scanning the QR at the venue or here: {{checkin.link}}\n\n{{org.name}}"),
    "post_invite": ("How did {{session.title}} go?",
                    "Hi {{participant.first_name}},\n\nThank you for attending {{session.title}}. Please complete the post-session assessment: {{assessment.link}}\n"
                    "And tell us what you thought: {{feedback.link}}\n\n{{org.name}}"),
    "post_reminder_1": ("Reminder: post-session assessment for {{session.title}}",
                        "Hi {{participant.first_name}},\n\nPlease complete your post-session assessment: {{assessment.link}}\n\nStop reminders: {{unsubscribe.link}}"),
    "post_reminder_2": ("Final reminder: {{session.title}} assessment closes soon",
                        "Hi {{participant.first_name}},\n\nThe post-session assessment closes in 24 hours: {{assessment.link}}\n\nStop reminders: {{unsubscribe.link}}"),
    "window_closed": ("Assessment window closed: {{session.title}}", "The post-session assessment window for {{session.title}} has closed.\n\n{{summary.text}}"),
    "cert_expiring": ("Certification expiring: {{certification.name}}",
                      "{{participant.name}}'s certification {{certification.name}} expires on {{certification.expires}}. Please arrange renewal."),
    "session_cancelled": ("Cancelled: {{session.title}}", "Hi {{participant.first_name}},\n\n{{session.title}} on {{session.start_local}} has been cancelled.\n{{summary.text}}\n\n{{org.name}}"),
    "session_updated": ("Updated: {{session.title}}", "Hi {{participant.first_name}},\n\n{{session.title}} has changed. It now starts {{session.start_local}} at {{session.location}} {{session.join_url}}.\n\n{{org.name}}"),
    "waitlist_promoted": ("A seat opened up: {{session.title}}", "Hi {{participant.first_name}},\n\nGood news - you're now registered for {{session.title}} on {{session.start_local}}.\n\n{{org.name}}"),
    "otp": ("Your check-in code", "Your one-time code is {{otp.code}}. It expires in 10 minutes."),
    "bounce_alert": ("Email bounced", "A message to {{participant.name}} could not be delivered and their email is now marked invalid. {{summary.text}}"),
    "rating_alert": ("Facilitator rating below threshold", "{{participant.name}}'s average participant feedback is now {{summary.text}}."),
}
EVENTS = tuple(DEFAULTS)
REMINDERS = {"pre_reminder_1", "pre_reminder_2", "post_reminder_1", "post_reminder_2"}  # the ones unsubscribe stops
NEEDS_PRE_PENDING = {"pre_reminder_1", "pre_reminder_2"}
NEEDS_POST_PENDING = {"post_reminder_1", "post_reminder_2"}
BACKOFF_MIN = (1, 5, 30)  # WFA-007


# ---------------------------------------------------------------- templates (WFA-003)


def check_fields(*texts: str) -> None:
    unknown = sorted({m for t in texts for m in FIELD.findall(t or "") if m not in MERGE_FIELDS})
    if unknown:
        raise HTTPException(422, f"Unknown merge field(s): {', '.join('{{' + u + '}}' for u in unknown)}. "
                                 f"Use one of: {', '.join(sorted(MERGE_FIELDS))}")


def template_for(db, tenant_id: int, event: str, channel: str = "email") -> dict:
    row = db.query(TrainingNotifyTemplate).filter_by(tenant_id=tenant_id, event=event, channel=channel).first() or \
        db.query(TrainingNotifyTemplate).filter_by(tenant_id=None, event=event, channel=channel).first()
    if row:
        return {"subject": row.subject, "body_text": row.body_text, "body_html": row.body_html, "custom": row.tenant_id is not None}
    subject, text = DEFAULTS.get(event, ("{{session.title}}", "{{summary.text}}"))
    return {"subject": subject, "body_text": text, "body_html": "", "custom": False}


def save_template(db, user: User, event: str, payload: dict) -> dict:
    policy.guard(db, user, "notify_templates", "u")
    if event not in EVENTS:
        raise HTTPException(404, "Unknown event")
    channel = payload.get("channel") or "email"
    subject, text, body_html = payload.get("subject") or "", payload.get("body_text") or "", payload.get("body_html") or ""
    if not text.strip():
        raise HTTPException(422, "A plain-text version is required")
    check_fields(subject, text, body_html)
    row = db.query(TrainingNotifyTemplate).filter_by(tenant_id=user.tenant_id, event=event, channel=channel).first()
    before = None if row is None else {"subject": row.subject, "body_text": row.body_text}
    if row is None:
        row = TrainingNotifyTemplate(tenant_id=user.tenant_id, event=event, channel=channel)
        db.add(row)
    row.subject, row.body_text, row.body_html = subject[:300], text, body_html
    policy.audit(db, user.tenant_id, user.id, "notify_template.save", "training_notify_template", event, before,
                 {"subject": subject, "body_text": text})
    db.flush()
    return template_for(db, user.tenant_id, event, channel)


def render(text: str, values: dict, escape: bool = False) -> str:
    def sub(m):
        value = str(values.get(m.group(1), ""))
        return html.escape(value) if escape else value
    return FIELD.sub(sub, text or "")


SAMPLE = {"participant.first_name": "Asha", "participant.name": "Asha Rao", "session.title": "Safe Working Practices",
          "session.start_local": "Mon 20 Oct 2026, 10:00 IST", "session.end_local": "Mon 20 Oct 2026, 13:00 IST",
          "session.location": "Bengaluru HQ, Room 4", "session.join_url": "", "assessment.link": "https://learning.example.com/p/a/…",
          "feedback.link": "https://learning.example.com/p/a/…", "qr.image": "https://learning.example.com/api/p/q/…/qr.png",
          "checkin.link": "https://learning.example.com/p/q/…", "org.name": "Your organization",
          "unsubscribe.link": "https://learning.example.com/p/u/…", "otp.code": "123456", "summary.text": "18 of 20 completed",
          "certification.name": "Certified Trainer", "certification.expires": "2026-12-01"}


def preview(db, user: User, event: str, payload: dict | None = None) -> dict:
    policy.guard(db, user, "notify_templates", "r")
    t = dict(template_for(db, user.tenant_id, event))
    if payload:
        t.update({k: v for k, v in payload.items() if k in ("subject", "body_text", "body_html")})
        check_fields(t["subject"], t["body_text"], t["body_html"])
    return {"subject": render(t["subject"], SAMPLE), "body_text": render(t["body_text"], SAMPLE),
            "body_html": render(t["body_html"], SAMPLE, escape=True) if t["body_html"] else
            "<p>" + html.escape(render(t["body_text"], SAMPLE)).replace("\n", "<br>") + "</p>"}


# ---------------------------------------------------------------- channel adapters (WFA-004)


class Adapter:
    """send(message) -> provider message id; raise to fail (and retry)."""
    name = "base"

    def send(self, to: str, subject: str, text: str, body_html: str, key: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class OutboxAdapter(Adapter):
    """Default: the message is kept in the training outbox (and the in-app
    messages of users with a login); nothing leaves the server."""
    name = "outbox"

    def send(self, to, subject, text, body_html, key):
        return f"outbox:{key}"


class SmtpAdapter(Adapter):
    name = "smtp"

    def send(self, to, subject, text, body_html, key):
        msg = EmailMessage()
        msg["From"] = os.environ.get("TRAINING_SMTP_FROM", "no-reply@localhost")
        msg["To"], msg["Subject"] = to, subject
        msg["Message-ID"] = f"<{hashlib.sha256(key.encode()).hexdigest()[:32]}@brt360>"
        msg.set_content(text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")
        host, port = os.environ["TRAINING_SMTP_HOST"], int(os.environ.get("TRAINING_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if os.environ.get("TRAINING_SMTP_TLS", "1") == "1":
                smtp.starttls()
            if os.environ.get("TRAINING_SMTP_USER"):
                smtp.login(os.environ["TRAINING_SMTP_USER"], os.environ.get("TRAINING_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        return msg["Message-ID"]


class NotConfiguredAdapter(Adapter):
    """SMS / WhatsApp Business (release 2): the provider is plugged in here."""
    def __init__(self, name):
        self.name = name

    def send(self, to, subject, text, body_html, key):
        raise RuntimeError(f"No {self.name} provider is configured")


ADAPTERS = {"outbox": OutboxAdapter(), "smtp": SmtpAdapter()}


def adapter_for(channel: str) -> Adapter:
    """Chosen by configuration, never by business logic (WFA-004)."""
    if channel == "email":
        return ADAPTERS.get(os.environ.get("TRAINING_EMAIL_ADAPTER", "outbox"), ADAPTERS["outbox"])
    return NotConfiguredAdapter(channel)


# ---------------------------------------------------------------- recipients and values


def _recipient(db, registration: TrainingRegistration | None, user_id: int | None = None) -> dict:
    if registration is not None and registration.participant_id:
        p = db.get(TrainingParticipant, registration.participant_id)
        return {"user_id": None, "participant_id": p.id, "email": p.email, "name": p.name,
                "invalid": p.email_invalid or bool(p.erased_at), "unsubscribed": p.unsubscribed}
    uid = registration.user_id if registration is not None else user_id
    u = db.get(User, uid)
    m = db.query(TrainingMember).filter_by(tenant_id=u.tenant_id, user_id=u.id).first()
    profile = (m.profile or {}) if m else {}
    return {"user_id": u.id, "participant_id": None, "email": u.email, "name": u.name,
            "invalid": bool(profile.get("email_invalid")), "unsubscribed": bool(profile.get("unsubscribed"))}


def _unsub_token(tenant_id: int, email: str) -> str:
    raw = f"{tenant_id}:{email}"
    sig = hmac.new(get_settings().brt_id_secret.encode(), f"unsub:{raw}".encode(), hashlib.sha256).hexdigest()[:24]
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=") + "." + sig


def unsubscribe(db, token: str) -> dict:
    """WFA-009: stops reminders (never mandatory invitations) across the org."""
    try:
        body, sig = token.split(".", 1)
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        tenant_id, email = raw.split(":", 1)
        tenant_id = int(tenant_id)
    except Exception:
        raise HTTPException(404, "This link is not valid")
    if not hmac.compare_digest(sig, _unsub_token(tenant_id, email).split(".", 1)[1]):
        raise HTTPException(404, "This link is not valid")
    p = db.query(TrainingParticipant).filter_by(tenant_id=tenant_id, email=email).first()
    if p:
        p.unsubscribed = True
    u = db.query(User).filter_by(tenant_id=tenant_id, email=email).first()
    if u:
        m = db.query(TrainingMember).filter_by(tenant_id=tenant_id, user_id=u.id).first()
        if m is None:
            m = TrainingMember(tenant_id=tenant_id, user_id=u.id, role="participant", location="")
            db.add(m)
        m.profile = {**(m.profile or {}), "unsubscribed": True}
    policy.audit(db, tenant_id, None, "notify.unsubscribe", "training_recipient", "", None, {"email_hash": hashlib.sha256(email.encode()).hexdigest()[:16]})
    db.commit()
    return {"unsubscribed": True}


def _tz(db, session: TrainingSession | None, tenant_id: int) -> str:
    if session is not None:
        if session.timezone:
            return session.timezone
        if session.location_id:
            loc = db.get(TrainingLocation, session.location_id)
            if loc:
                return loc.timezone
    return policy.settings(db, tenant_id)["timezone"]


def _fmt(value: dt.datetime | None, tz: str) -> str:
    if value is None:
        return ""
    local = value.replace(tzinfo=dt.timezone.utc).astimezone(policy.zone(tz))
    return local.strftime("%a %d %b %Y, %H:%M ") + (local.tzname() or tz)


def _link(db, registration_id: int | None, phase: str) -> str:
    if not registration_id:
        return ""
    inv = db.query(TrainingInvitation).filter_by(registration_id=registration_id, phase=phase).first()
    if inv is None or inv.instance_id is None:
        return ""
    from app.crm_core.training_assess import link_for, public_url
    return public_url(link_for(inv))


def values_for(db, n: TrainingNotification, extra: dict | None = None) -> dict:
    from app.crm_core.training_assess import public_url
    tenant = db.get(Tenant, n.tenant_id)
    session = db.get(TrainingSession, n.session_id) if n.session_id else None
    tz = _tz(db, session, n.tenant_id)
    name = ""
    if n.recipient_participant_id:
        name = db.get(TrainingParticipant, n.recipient_participant_id).name
    elif n.recipient_user_id:
        name = db.get(User, n.recipient_user_id).name
    v = {"participant.name": name, "participant.first_name": (name or "").split(" ")[0], "org.name": tenant.display_name or tenant.name,
         "unsubscribe.link": public_url(f"/p/u/{_unsub_token(n.tenant_id, n.recipient_email)}")}
    if session is not None:
        loc = db.get(TrainingLocation, session.location_id) if session.location_id else None
        place = ", ".join(x for x in [loc.name if loc else session.location, session.room or ""] if x)
        v.update({"session.title": session.title, "session.start_local": _fmt(session.starts_at, tz),
                  "session.end_local": _fmt(session.ends_at, tz), "session.location": place or "online",
                  "session.join_url": session.virtual_url or "", "checkin.link": public_url(f"/p/q/{session.checkin_code}"),
                  "qr.image": public_url(f"/api/p/q/{session.checkin_code}/qr.png"),
                  "assessment.link": _link(db, n.registration_id, "post" if n.event.startswith("post") else "pre"),
                  "feedback.link": _link(db, n.registration_id, "feedback")})
    v.update(extra or {})
    return v


# ---------------------------------------------------------------- planning (WFA-001)


def queue(db, session: TrainingSession | None, registration: TrainingRegistration | None, event: str, when: dt.datetime,
          user_id: int | None = None, extra: dict | None = None, send_now: bool = False, key: str | None = None) -> TrainingNotification | None:
    tenant_id = session.tenant_id if session is not None else db.get(User, user_id).tenant_id
    who = _recipient(db, registration, user_id)
    key = key or f"{tenant_id}:{session.id if session else 0}:{event}:{registration.id if registration else 'u' + str(user_id)}:{when:%Y%m%d%H%M}"
    if extra and "otp.code" in extra:
        key += f":{utcnow().timestamp()}"
    existing = db.query(TrainingNotification).filter_by(idempotency_key=key).first()
    if existing:
        return existing
    n = TrainingNotification(tenant_id=tenant_id, session_id=session.id if session else None,
                             registration_id=registration.id if registration else None, event=event, channel="email",
                             recipient_user_id=who["user_id"], recipient_participant_id=who["participant_id"],
                             recipient_email=who["email"], scheduled_at=when, status="scheduled", attempts=0, idempotency_key=key)
    if extra:
        # secrets such as a one-time code are rendered now and never stored in the row unrendered
        t = template_for(db, tenant_id, event)
        values = values_for(db, n, extra)
        n.subject, n.body_text = render(t["subject"], values), render(t["body_text"], values)
    db.add(n)
    db.flush()
    if send_now:
        _send(db, n, utcnow())
    return n


def _plan(session: TrainingSession, settings: dict, post_closes: dt.datetime | None) -> list[tuple[str, dt.datetime, str]]:
    r = settings["reminders"]
    plan = [("pre_reminder_1", session.starts_at - dt.timedelta(hours=r["pre_1_h"]), "registered"),
            ("pre_reminder_2", session.starts_at - dt.timedelta(hours=r["pre_2_h"]), "registered"),
            ("session_day", session.starts_at - dt.timedelta(hours=r["session_day_h"]), "registered"),
            ("post_invite", session.ends_at + dt.timedelta(minutes=r["post_send_min"]), "attended"),
            ("post_reminder_1", session.ends_at + dt.timedelta(hours=r["post_1_h"]), "attended")]
    if post_closes:
        plan.append(("post_reminder_2", post_closes - dt.timedelta(hours=r["post_2_before_close_h"]), "attended"))
    return plan


def plan_session(db, session: TrainingSession, rescheduled: bool = False) -> None:
    """(Re)computes every pending send; rescheduling moves them (WFA-001)."""
    if session.status == "cancelled":
        return
    from app.crm_core.training_delivery import facilitators_of, status_of
    settings = policy.settings(db, session.tenant_id, session)
    now = utcnow()
    post = db.query(TrainingInstance).filter_by(session_id=session.id, type="post").first()
    has_pre = db.query(TrainingInstance).filter_by(session_id=session.id, type="pre").first() is not None
    wanted: set[str] = set()
    regs = db.query(TrainingRegistration).filter_by(session_id=session.id).all()
    for reg in regs:
        if status_of(reg) not in ("registered", "attended"):
            continue
        for event, when, _who in _plan(session, settings, post.closes_at if post else None):
            if event in NEEDS_PRE_PENDING and not has_pre or event.startswith("post") and post is None:
                continue
            if when <= now and not rescheduled:
                continue  # never send a stale reminder
            n = queue(db, session, reg, event, max(when, now))
            wanted.add(n.idempotency_key)
    for fid in facilitators_of(session):
        when = session.starts_at - dt.timedelta(hours=settings["reminders"]["session_day_h"])
        if when > now:
            wanted.add(queue(db, session, None, "session_day", when, user_id=fid).idempotency_key)
    # anything still planned that the current plan no longer contains is cancelled
    for n in db.query(TrainingNotification).filter_by(session_id=session.id, status="scheduled").all():
        if n.event in {e for e, _, _ in _plan(session, settings, None)} | {"post_reminder_2", "session_day"} and n.idempotency_key not in wanted:
            n.status, n.skip_reason = "cancelled", "replanned"
    if rescheduled:
        for reg in regs:
            if status_of(reg) in ("registered", "waitlisted"):
                queue(db, session, reg, "session_updated", now, key=f"{session.tenant_id}:{session.id}:updated:{reg.id}:{session.starts_at:%Y%m%d%H%M}")
    db.flush()


def on_registered(db, registration: TrainingRegistration) -> None:
    session = db.get(TrainingSession, registration.session_id)
    queue(db, session, registration, "registration_confirmed", utcnow())
    plan_session(db, session)


def cancel_pending(db, session_id: int | None = None, registration_id: int | None = None) -> int:
    q = db.query(TrainingNotification).filter(TrainingNotification.status.in_(("scheduled", "queued")))
    q = q.filter_by(session_id=session_id) if session_id else q.filter_by(registration_id=registration_id)
    rows = q.all()
    for n in rows:
        n.status, n.skip_reason = "cancelled", "session cancelled" if session_id else "registration cancelled"
    db.flush()
    return len(rows)


def on_cancel(db, session: TrainingSession) -> None:
    """BR-WFA-01: everything pending stops; only the cancellation notice goes."""
    from app.crm_core.training_delivery import facilitators_of, status_of
    cancel_pending(db, session_id=session.id)
    now = utcnow()
    for reg in db.query(TrainingRegistration).filter_by(session_id=session.id).all():
        if status_of(reg) in ("registered", "waitlisted", "nominated"):
            queue(db, session, reg, "session_cancelled", now, extra={"summary.text": session.status_reason or ""},
                  key=f"{session.tenant_id}:{session.id}:cancelled:{reg.id}")
    for fid in facilitators_of(session):
        queue(db, session, None, "session_cancelled", now, user_id=fid, extra={"summary.text": session.status_reason or ""},
              key=f"{session.tenant_id}:{session.id}:cancelled:u{fid}")


# ---------------------------------------------------------------- sending


def _quiet(db, n: TrainingNotification, settings: dict, now: dt.datetime) -> dt.datetime | None:
    """WFA-005: the next 08:00 local when now is inside quiet hours."""
    quiet = settings["quiet_hours"]
    if not quiet.get("enabled") or n.event == "otp":
        return None
    session = db.get(TrainingSession, n.session_id) if n.session_id else None
    tz = policy.zone(_tz(db, session, n.tenant_id))
    local = now.replace(tzinfo=dt.timezone.utc).astimezone(tz)
    start, end = int(quiet["start"]), int(quiet["end"])
    inside = local.hour >= start or local.hour < end if start > end else start <= local.hour < end
    if not inside:
        return None
    resume = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if resume <= local:
        resume += dt.timedelta(days=1)
    return resume.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _skip_reason(db, n: TrainingNotification) -> str | None:
    """WFA-002 / BR-WFA-01 / BR-ASM-01 checks, made at send time."""
    from app.crm_core.training_delivery import status_of
    session = db.get(TrainingSession, n.session_id) if n.session_id else None
    if session is not None and session.status == "cancelled" and n.event != "session_cancelled":
        return "session cancelled"
    reg = db.get(TrainingRegistration, n.registration_id) if n.registration_id else None
    if reg is not None:
        state = status_of(reg)
        if state == "cancelled" and n.event != "session_cancelled":
            return "registration cancelled"
        if n.event in NEEDS_PRE_PENDING or n.event == "registration_confirmed":
            inv = db.query(TrainingInvitation).filter_by(registration_id=reg.id, phase="pre").first()
            if n.event in NEEDS_PRE_PENDING and (inv is None or inv.has_submitted):
                return "pre-assessment already completed" if inv else "no pre-assessment"
        if n.event == "post_invite" or n.event in NEEDS_POST_PENDING:
            if state != "attended":
                return "did not attend"
            from app.crm_core.training_assess import issue_post
            if session is not None and session.status in ("completed", "closed"):
                issue_post(db, session)
            inv = db.query(TrainingInvitation).filter_by(registration_id=reg.id, phase="post").first()
            if inv is None:
                return "post-assessment not open yet"
            if n.event in NEEDS_POST_PENDING and inv.has_submitted:
                return "post-assessment already completed"
    who = _recipient(db, reg, n.recipient_user_id) if (reg is not None or n.recipient_user_id) else None
    if who and who["invalid"]:
        return "email marked invalid"
    if who and who["unsubscribed"] and n.event in REMINDERS:
        return "unsubscribed from reminders"
    return None


def _send(db, n: TrainingNotification, now: dt.datetime, manual: bool = False) -> None:
    claimed = db.execute(update(TrainingNotification).where(TrainingNotification.id == n.id,
                                                            TrainingNotification.status.in_(("scheduled", "queued")))
                         .values(status="sending"))
    if claimed.rowcount != 1:
        return  # someone else has it - the idempotency key guards the provider side too
    db.refresh(n)
    reason = _skip_reason(db, n)
    if reason:
        n.status, n.skip_reason = ("cancelled" if "cancelled" in reason else "skipped"), reason
        db.flush()
        return
    settings = policy.settings(db, n.tenant_id)
    later = None if manual else _quiet(db, n, settings, now)  # "send now" by HR is deliberate
    if later:
        n.status, n.scheduled_at = "queued", later
        db.flush()
        return
    if not n.body_text:
        t = template_for(db, n.tenant_id, n.event, n.channel)
        values = values_for(db, n)
        n.subject = render(t["subject"], values)[:300]
        n.body_text = render(t["body_text"], values)
        n.body_html = render(t["body_html"], values, escape=True) if t["body_html"] else None
    try:
        n.provider_message_id = adapter_for(n.channel).send(n.recipient_email, n.subject, n.body_text, n.body_html or "", n.idempotency_key)
        n.status, n.sent_at, n.error = "sent", now, None
        if n.registration_id and n.event in ("registration_confirmed", "post_invite"):
            phase = "pre" if n.event == "registration_confirmed" else "post"
            inv = db.query(TrainingInvitation).filter_by(registration_id=n.registration_id, phase=phase).first()
            if inv is not None:
                inv.sent_at = now
    except Exception as exc:  # retry 1, 5, 30 minutes, then failed (WFA-007)
        n.attempts += 1
        n.error = str(exc)[:1000]
        if n.attempts <= len(BACKOFF_MIN):
            n.status, n.next_attempt_at = "queued", now + dt.timedelta(minutes=BACKOFF_MIN[n.attempts - 1])
        else:
            n.status = "failed"
    db.flush()


def dispatch_due(db, tenant_id: int, now=None) -> int:
    now = now or utcnow()
    due = db.query(TrainingNotification).filter(TrainingNotification.tenant_id == tenant_id,
                                                TrainingNotification.status.in_(("scheduled", "queued")),
                                                TrainingNotification.scheduled_at <= now).all()
    sent = 0
    for n in due:
        if n.next_attempt_at and n.next_attempt_at > now:
            continue
        _send(db, n, now)
        sent += n.status == "sent"
    return sent


def mark_clicked(db, registration_id: int, phase: str) -> None:
    events = ("registration_confirmed", "pre_reminder_1", "pre_reminder_2") if phase == "pre" else ("post_invite", "post_reminder_1", "post_reminder_2")
    for n in db.query(TrainingNotification).filter(TrainingNotification.registration_id == registration_id,
                                                   TrainingNotification.event.in_(events),
                                                   TrainingNotification.status.in_(("sent", "delivered", "opened"))).all():
        n.status = "clicked"


def record_status(db, user: User, notification_id: int, status: str, hard: bool = False) -> TrainingNotification:
    """WFA-006 provider callbacks (delivered / bounced / opened / clicked)."""
    policy.guard(db, user, "notifications", "u")
    n = db.query(TrainingNotification).filter_by(id=notification_id, tenant_id=user.tenant_id).first()
    if n is None:
        raise HTTPException(404, "Record not found")
    if status not in ("delivered", "bounced", "opened", "clicked", "failed"):
        raise HTTPException(422, "Unknown delivery status")
    n.status = status
    if status == "bounced" and hard:
        _hard_bounce(db, n)
    db.flush()
    return n


def _hard_bounce(db, n: TrainingNotification) -> None:
    if n.recipient_participant_id:
        p = db.get(TrainingParticipant, n.recipient_participant_id)
        p.email_invalid = True
        name = p.name
    else:
        m = db.query(TrainingMember).filter_by(tenant_id=n.tenant_id, user_id=n.recipient_user_id).first()
        if m:
            m.profile = {**(m.profile or {}), "email_invalid": True}
        name = db.get(User, n.recipient_user_id).name if n.recipient_user_id else n.recipient_email
    session = db.get(TrainingSession, n.session_id) if n.session_id else None
    for uid in staff_ids(db, n.tenant_id, ("hr",)) or staff_ids(db, n.tenant_id, ("admin",)):
        queue(db, session, None, "bounce_alert", utcnow(), user_id=uid,
              extra={"participant.name": name, "summary.text": session.title if session else ""},
              key=f"{n.tenant_id}:bounce:{n.id}:u{uid}")


def opened_token(notification_id: int) -> str:
    return hmac.new(get_settings().brt_id_secret.encode(), f"open:{notification_id}".encode(), hashlib.sha256).hexdigest()[:24]


def mark_opened(db, notification_id: int, token: str) -> None:
    if not hmac.compare_digest(token or "", opened_token(notification_id)):
        return
    n = db.get(TrainingNotification, notification_id)
    if n is not None and n.status in ("sent", "delivered"):
        n.status = "opened"
        db.commit()


def staff_ids(db, tenant_id: int, held: tuple[str, ...]) -> list[int]:
    out = []
    if "admin" in held:
        out += [u.id for u in db.query(User).filter_by(tenant_id=tenant_id, role=UserRole.admin, is_active=True)]
    for m in db.query(TrainingMember).filter_by(tenant_id=tenant_id).all():
        if ({m.role, *(m.roles or [])} & set(held)) and m.user_id not in out:
            out.append(m.user_id)
    return out


def resend(db, user: User, registration_ids: list[int], phase: str, regenerate: bool) -> dict:
    """WFA-008: send the link again now - the same link unless regenerate is chosen."""
    policy.guard(db, user, "notifications", "c")
    from app.crm_core.training_assess import regenerate as regen
    from app.crm_core.training_delivery import session_for
    done = []
    for rid in registration_ids:
        reg = db.query(TrainingRegistration).filter_by(id=rid, tenant_id=user.tenant_id).first()
        if reg is None:
            raise HTTPException(404, "Record not found")
        session, _ = session_for(db, user, reg.session_id, "r")
        inv = db.query(TrainingInvitation).filter_by(registration_id=reg.id, phase=phase).first()
        if inv is None or inv.instance_id is None:
            continue
        if regenerate:
            regen(db, inv)
        event = "registration_confirmed" if phase == "pre" else "post_invite"
        n = queue(db, session, reg, event, utcnow(), key=f"{user.tenant_id}:resend:{reg.id}:{phase}:{utcnow().timestamp()}")
        _send(db, n, utcnow(), manual=True)
        done.append({"registration_id": reg.id, "status": n.status})
        policy.audit(db, user.tenant_id, user.id, "notify.resend", "training_registration", reg.id, None, {"phase": phase, "regenerate": regenerate})
    return {"resent": done}


def session_log(db, user: User, session_id: int) -> list[dict]:
    """WFA-010: every planned and sent message, without the message bodies."""
    policy.guard(db, user, "notifications", "r")
    from app.crm_core.training_delivery import session_for
    session_for(db, user, session_id, "r")
    rows = db.query(TrainingNotification).filter_by(tenant_id=user.tenant_id, session_id=session_id).order_by(TrainingNotification.scheduled_at).all()
    return [{"id": n.id, "event": n.event, "channel": n.channel, "recipient": n.recipient_email, "scheduled_at": n.scheduled_at.isoformat() + "Z",
             "sent_at": n.sent_at.isoformat() + "Z" if n.sent_at else None, "status": n.status, "attempts": n.attempts,
             "reason": n.skip_reason or n.error, "registration_id": n.registration_id} for n in rows]


def my_messages(db, user: User) -> list[dict]:
    rows = db.query(TrainingNotification).filter(TrainingNotification.tenant_id == user.tenant_id,
                                                 TrainingNotification.recipient_user_id == user.id,
                                                 TrainingNotification.status.in_(("sent", "delivered", "opened", "clicked"))) \
        .order_by(TrainingNotification.sent_at.desc()).limit(100).all()
    return [{"id": n.id, "event": n.event, "subject": n.subject, "body": n.body_text, "sent_at": n.sent_at.isoformat() + "Z" if n.sent_at else None}
            for n in rows if n.event != "otp"]


# ---------------------------------------------------------------- the minute tick


def tick(db, tenant_id: int, now=None) -> dict:
    """Session lifecycle, closed windows, certification alerts, sends, purge."""
    now = now or utcnow()
    from app.crm_core import training_analytics as analytics
    from app.crm_core.training_delivery import auto_transitions, facilitators_of
    moved = auto_transitions(db, tenant_id, now)
    for inst in db.query(TrainingInstance).filter(TrainingInstance.tenant_id == tenant_id, TrainingInstance.closed_handled.is_(False),
                                                  TrainingInstance.closes_at <= now).all():
        inst.closed_handled = True
        session = db.get(TrainingSession, inst.session_id)
        if inst.type != "post" or session.status == "cancelled":
            continue
        analytics.recompute(db, session)
        submitted = db.query(TrainingInvitation).filter(TrainingInvitation.instance_id == inst.id, TrainingInvitation.has_submitted.is_(True)).count()
        invited = db.query(TrainingInvitation).filter_by(instance_id=inst.id).count()
        for uid in set(facilitators_of(session)) | set(staff_ids(db, tenant_id, ("hr", "admin"))):
            queue(db, session, None, "window_closed", now, user_id=uid, extra={"summary.text": f"{submitted} of {invited} completed the post-assessment."},
                  key=f"{tenant_id}:{session.id}:closed:u{uid}")
    settings = policy.settings(db, tenant_id)
    for cert in db.query(TrainingCertificate).filter_by(tenant_id=tenant_id).all():
        if cert.revoked or cert.expires_at <= now:
            continue
        for days in settings["reminders"]["cert_days"]:
            if now >= cert.expires_at - dt.timedelta(days=int(days)):
                holder = db.get(User, cert.user_id)
                for uid in {cert.user_id, *staff_ids(db, tenant_id, ("admin",))}:
                    queue(db, None, None, "cert_expiring", now, user_id=uid,
                          extra={"participant.name": holder.name, "certification.name": cert.name,
                                 "certification.expires": cert.expires_at.strftime("%Y-%m-%d")},
                          key=f"{tenant_id}:cert:{cert.id}:{days}:u{uid}")  # once per threshold (FAC-005)
    _rating_alerts(db, tenant_id, settings)
    sent = dispatch_due(db, tenant_id, now)
    purged = purge(db, tenant_id, settings, now)
    db.flush()
    return {"transitions": moved, "sent": sent, "purged": purged}


def _rating_alerts(db, tenant_id: int, settings: dict) -> None:
    """FAC-005: alert Org Admins once when a facilitator's average rating
    crosses below the threshold (again only after it recovers)."""
    from app.crm_core import training_analytics as analytics
    for m in db.query(TrainingMember).filter_by(tenant_id=tenant_id).all():
        if "facilitator" not in {m.role, *(m.roles or [])}:
            continue
        admin = db.query(User).filter_by(tenant_id=tenant_id, role=UserRole.admin, is_active=True).first()
        if admin is None:
            return
        card = analytics.scorecard(db, admin, m.user_id)
        below = card["below_threshold"]
        profile = dict(m.profile or {})
        if below and not profile.get("rating_alerted"):
            for uid in staff_ids(db, tenant_id, ("admin",)):
                queue(db, None, None, "rating_alert", utcnow(), user_id=uid,
                      extra={"participant.name": card["facilitator"]["name"], "summary.text": f"{card['average_feedback']:.2f} / 5"},
                      key=f"{tenant_id}:rating:{m.user_id}:{utcnow():%Y%m%d%H%M}:u{uid}")
            profile["rating_alerted"] = True
        elif not below and profile.get("rating_alerted"):
            profile["rating_alerted"] = False
        if profile != (m.profile or {}):
            m.profile = profile


def purge(db, tenant_id: int, settings: dict, now: dt.datetime) -> int:
    """NFR-004 retention: directory participants with no session within the
    retention period are erased; drafts of closed assessments are dropped."""
    cutoff = now - dt.timedelta(days=30 * int(settings["retention_months"]))
    erased = 0
    for p in db.query(TrainingParticipant).filter(TrainingParticipant.tenant_id == tenant_id, TrainingParticipant.erased_at.is_(None)).all():
        regs = db.query(TrainingRegistration).filter_by(participant_id=p.id).all()
        last = max([db.get(TrainingSession, r.session_id).ends_at for r in regs] or [p.sys_created_at or now])
        if last < cutoff:
            token = "ERASED-" + hashlib.sha256(f"{p.id}:{p.email}".encode()).hexdigest()[:12].upper()
            p.name, p.email, p.phone, p.employee_id, p.manager, p.organization = token, f"{token.lower()}@erased.invalid", "", "", "", ""
            p.status, p.erased_at = "erased", now
            erased += 1
    if erased:
        policy.audit(db, tenant_id, None, "retention.purge", "training_participant", "", None, {"erased": erased})
    for inv in db.query(TrainingInvitation).filter(TrainingInvitation.tenant_id == tenant_id, TrainingInvitation.draft.isnot(None),
                                                   TrainingInvitation.expires_at < now).all():
        inv.draft = None
    return erased
