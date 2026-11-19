"""Durable owner status events in the canonical application database.

This queue cannot approve applications, send messages or start employer delivery.
"""

import json
import sqlite3
from datetime import UTC, datetime


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
    expected = {"job_found": {"discovered", "shortlisted", "review_ready"},
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
