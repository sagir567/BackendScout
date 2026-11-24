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


def test_owner_events_follow_caller_rollback(db):
    db.commit()
    assert enqueue_owner_status(db, "job", "production", "job_found", "rollback-example")
    db.rollback()
    assert db.execute("SELECT count(*) FROM owner_notification_events").fetchone()[0] == 0


def test_pending_dispatch_disabled_never_opens_database(tmp_path):
    from types import SimpleNamespace

    from backend_scout.config import Settings
    from backend_scout.owner_notifications import dispatch_pending_owner_notifications

    nonexistent = tmp_path / "must-not-create.sqlite3"
    store = SimpleNamespace(database_path=nonexistent)
    assert dispatch_pending_owner_notifications(store, Settings(_env_file=None)) == []
    assert not nonexistent.exists()


def test_owner_events_survive_reopen_without_resubmission(tmp_path):
    from backend_scout.application_store import ApplicationStore
    from backend_scout.config import TrackerName
    from backend_scout.models import Job

    root = tmp_path / "private"
    root.mkdir()
    path = tmp_path / "workflow.sqlite3"
    store = ApplicationStore(path, root)
    job = Job(source="manual", source_url="https://example.test/jobs/one",
              company="Example", title="Backend Engineer", description="Build APIs and background services.")
    store.import_snapshot("one", TrackerName.PRODUCTION, job, "interview")
    assert store.queue_owner_status_update("one", TrackerName.PRODUCTION, "interview", "message-1")
    reopened = ApplicationStore(path, root)
    assert not reopened.queue_owner_status_update("one", TrackerName.PRODUCTION, "interview", "message-1")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM owner_notification_events").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("case,expected", [("ok", "accepted"), ("network", "unknown"),
                                         ("budget", "budget_blocked"), ("price", "missing_pricing")])
def test_dispatch_reserves_before_send_and_never_replays_unknown(db, tmp_path, case, expected):
    from types import SimpleNamespace

    from backend_scout.owner_notifications import dispatch_owner_event
    from backend_scout.owner_whatsapp import OwnerWhatsAppConfig

    enqueue_owner_status(db, "job", "production", "job_found", "source-1")
    db.commit()
    path = tmp_path / "canonical.sqlite3"
    with sqlite3.connect(path) as target:
        db.backup(target)
    calls = []

    def reserve(*_):
        calls.append("budget")
        if case == "budget":
            raise ValueError("quota")

    def send(_):
        assert calls == ["budget"]
        calls.append("send")
        if case == "network":
            raise RuntimeError("private provider diagnostic")
        return "wamid.example"

    config = OwnerWhatsAppConfig(enabled=True, access_token="test", phone_number_id="12345",
                                 owner_number="972500000000", template_name="job_status")
    client = SimpleNamespace(config=config, send_template=send)
    budget = SimpleNamespace(record_external_usage=reserve)
    price = None if case == "price" else "0.05"
    assert dispatch_owner_event(path, 1, "production", client, budget, price) == expected
    if case in {"ok", "network"}:
        before = calls.copy()
        assert dispatch_owner_event(path, 1, "production", client, budget, price) == "not_pending"
        assert calls == before
    else:
        assert "send" not in calls
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT status FROM applications").fetchone()[0] == "review_ready"
        assert connection.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 0


def test_dispatch_shared_budget_and_concurrent_claim(db, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from backend_scout.browser_budget import BrowserBudget, install_budget_schema
    from backend_scout.owner_notifications import dispatch_owner_event
    from backend_scout.owner_whatsapp import OwnerWhatsAppConfig

    enqueue_owner_status(db, "job", "production", "job_found", "source-1")
    enqueue_owner_status(db, "job", "production", "job_found", "source-2")
    install_budget_schema(db)
    db.commit()
    path = tmp_path / "canonical.sqlite3"
    with sqlite3.connect(path) as target:
        db.backup(target)
    budget = BrowserBudget(path, total_monthly_limit_nis="1", fixed_monthly_nis="0.95")
    config = OwnerWhatsAppConfig(enabled=True, access_token="test", phone_number_id="12345",
                                 owner_number="972500000000", template_name="job_status")
    sent = []

    def send(summary):
        assert budget.snapshot("job", "production")["monthly_external_micro_nis"] == 50_000
        sent.append(summary)
        return "wamid.example"

    client = SimpleNamespace(config=config, send_template=send)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: dispatch_owner_event(
            path, 1, "production", client, budget, "0.05",
        ), range(2)))
    assert sorted(results) == ["accepted", "not_pending"]
    assert len(sent) == 1
    assert dispatch_owner_event(path, 2, "production", client, budget, "0.05") == "budget_blocked"
    assert len(sent) == 1
