"""Durable owner status events in the canonical application database.

This queue cannot approve applications, send messages or start employer delivery.
"""

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


def install_owner_notification_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS owner_notification_events (
        id INTEGER PRIMARY KEY,
        application_pk INTEGER NOT NULL REFERENCES applications(id),
        event_type TEXT NOT NULL CHECK(event_type IN ('job_found','submitted','rejected','interview','offer')),
        source_event_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','delivered','unknown','failed')),
        provider_message_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(application_pk,event_type,source_event_id)
    )""")


def enqueue_owner_status(db: sqlite3.Connection, application_id: str, tracker: str,
                         event_type: str, source_event_id: str) -> bool:
    """Caller owns transaction; derive message only from current canonical facts."""
    expected = {"job_found": {"discovered", "shortlisted", "review_ready", "needs_input"},
                "submitted": {"submitted"}, "rejected": {"rejected"},
                "interview": {"interview"}, "offer": {"offer"}}
    if event_type not in expected or not source_event_id.strip() or len(source_event_id) > 200:
        raise ValueError("Invalid owner notification event")
    if tracker not in {"test", "production"}:
        raise ValueError("Invalid tracker")
    row = db.execute(
        "SELECT id,status,job_json FROM applications WHERE application_id=? AND tracker=?",
        (application_id, tracker),
    ).fetchone()
    if row is None or row[1] not in expected[event_type]:
        raise ValueError("Notification does not match canonical application status")
    if event_type == "submitted" and not db.execute(
        "SELECT 1 FROM delivery_attempts WHERE application_pk=? AND state='submitted' "
        "AND proof_path IS NOT NULL AND proof_sha256 IS NOT NULL LIMIT 1", (row[0],),
    ).fetchone():
        raise ValueError("Confirmed submission evidence is required")
    job = json.loads(row[2])
    label = {"job_found": "New opportunity", "submitted": "Application submitted",
             "rejected": "Application rejected", "interview": "Interview update", "offer": "Offer update"}[event_type]
    summary = " ".join(f"{label}: {job['company']} - {job['title']}".split())[:700]
    result = db.execute(
        "INSERT OR IGNORE INTO owner_notification_events "
        "(application_pk,event_type,source_event_id,summary,created_at) VALUES (?,?,?,?,?)",
        (row[0], event_type, source_event_id, summary, datetime.now(UTC).isoformat()),
    )
    return result.rowcount == 1


def dispatch_owner_event(database_path, event_id, tracker, client, budget, unit_cost_nis):
    """Explicit one-shot dispatch; unknown acknowledgements are never replayed.

    Claim before reserving cost. A crash after claim needs reconciliation, not
    lease expiry. Conservative budget holds remain booked on ambiguous sends.
    This function has no application-state write or employer delivery operation.
    """
    if not client.config.enabled or client.config.missing_fields():
        return "disabled_or_unconfigured"
    try:
        price = Decimal(str(unit_cost_nis))
        if not price.is_finite() or price <= 0:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        return "missing_pricing"
    if tracker not in {"test", "production"}:
        raise ValueError("Invalid tracker")
    with sqlite3.connect(database_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT e.summary,e.status,e.event_type,a.status FROM owner_notification_events e "
            "JOIN applications a ON a.id=e.application_pk WHERE e.id=? AND a.tracker=?",
            (event_id, tracker),
        ).fetchone()
        if row is None or row[1] != "pending":
            return "not_pending"
        states = {"job_found": {"discovered", "shortlisted", "review_ready", "needs_input"},
                  "submitted": {"submitted"}, "rejected": {"rejected"},
                  "interview": {"interview"}, "offer": {"offer"}}
        if row[3] not in states[row[2]]:
            db.execute("UPDATE owner_notification_events SET status='failed' WHERE id=?", (event_id,))
            return "superseded"
        db.execute("UPDATE owner_notification_events SET status='unknown' WHERE id=?", (event_id,))
    try:
        budget.record_external_usage(f"owner-whatsapp:{tracker}:{event_id}", price)
    except (ValueError, OSError, sqlite3.Error):
        with sqlite3.connect(database_path) as db:
            db.execute("UPDATE owner_notification_events SET status='pending' WHERE id=? AND status='unknown'",
                       (event_id,))
        return "budget_blocked"
    try:
        message_id = client.send_template(row[0])
    except Exception:  # noqa: BLE001 - no secrets from provider exceptions enter the queue.
        return "unknown"
    with sqlite3.connect(database_path) as db:
        db.execute("UPDATE owner_notification_events SET status='accepted',provider_message_id=? "
                   "WHERE id=? AND status='unknown'", (message_id, event_id))
    return "accepted"


def dispatch_pending_owner_notifications(store, settings, *, client=None, budget=None):
    """General worker only; disabled/missing configuration is a network-free no-op."""
    from backend_scout.budget_config import configured_budget
    from backend_scout.owner_whatsapp import OwnerWhatsAppClient, OwnerWhatsAppConfig

    config = OwnerWhatsAppConfig.from_settings(settings)
    if not config.enabled or config.missing_fields() or settings.owner_whatsapp_unit_cost_nis is None:
        return []
    client = client or OwnerWhatsAppClient(config)
    budget = budget or configured_budget(settings)
    with sqlite3.connect(store.database_path) as db:
        event_ids = [row[0] for row in db.execute(
            "SELECT e.id FROM owner_notification_events e JOIN applications a ON a.id=e.application_pk "
            "WHERE a.tracker='production' AND e.status='pending' ORDER BY e.id LIMIT 3",
        )]
    outcomes = []
    for event_id in event_ids:
        outcome = dispatch_owner_event(store.database_path, event_id, "production", client,
                                       budget, settings.owner_whatsapp_unit_cost_nis)
        outcomes.append(outcome)
        if outcome in {"budget_blocked", "missing_pricing", "disabled_or_unconfigured", "unknown"}:
            break
    return outcomes
