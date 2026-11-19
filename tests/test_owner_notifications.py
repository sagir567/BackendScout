import json
import sqlite3

import pytest

from backend_scout.owner_notifications import (
    enqueue_owner_status,
    install_owner_notification_schema,
)


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE applications(id INTEGER PRIMARY KEY, application_id, tracker,status,job_json)")
    connection.execute("CREATE TABLE delivery_attempts(application_pk,state,proof_path,proof_sha256)")
    install_owner_notification_schema(connection)
    connection.execute("INSERT INTO applications VALUES (1,'job','production','review_ready',?)", (
        json.dumps({"company": "Example", "title": "Engineer", "email": "must-not-leak@example.test"}),
    ))
    yield connection
    connection.close()


def test_durable_event_dedup_and_private_summary(db):
    assert enqueue_owner_status(db, "job", "production", "job_found", "source-1")
    db.commit()
    install_owner_notification_schema(db)
    assert not enqueue_owner_status(db, "job", "production", "job_found", "source-1")
    assert db.execute("SELECT summary,status FROM owner_notification_events").fetchall() == [
        ("New opportunity: Example - Engineer", "pending"),
    ]


@pytest.mark.parametrize("event", ["submitted", "rejected", "interview", "offer"])
def test_unconfirmed_state_never_notifies(db, event):
    with pytest.raises(ValueError, match="canonical"):
        enqueue_owner_status(db, "job", "production", event, "source-1")
    assert db.execute("SELECT count(*) FROM owner_notification_events").fetchone()[0] == 0


def test_submission_requires_evidence_and_tracker_matches(db):
    db.execute("UPDATE applications SET status='submitted'")
    with pytest.raises(ValueError, match="evidence"):
        enqueue_owner_status(db, "job", "production", "submitted", "receipt-1")
    db.execute("INSERT INTO delivery_attempts VALUES (1,'submitted','proof.jpg','hash')")
    assert enqueue_owner_status(db, "job", "production", "submitted", "receipt-1")
    with pytest.raises(ValueError, match="canonical"):
        enqueue_owner_status(db, "job", "test", "submitted", "receipt-1")
