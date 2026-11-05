import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend_scout.application_package import ApplicationPackage
from backend_scout.application_store import ApplicationConflict, ApplicationStore, is_ledger_owned
from backend_scout.config import TrackerName
from backend_scout.models import Job
from backend_scout.task_queue import QueuedTaskKind, enqueue_task, list_tasks


def package(**updates):
    return ApplicationPackage.model_validate(
        {
            "application_id": "example-job",
            "tracker": "test",
            "company": "Example",
            "role": "Backend Engineer",
            "official_destination": "https://jobs.example.test/123",
            "delivery_route": "portal",
            "cv_version": "v1",
            "cv_sha256": hashlib.sha256(b"reviewed CV").hexdigest(),
            "answers": [],
        }
        | updates
    )


@pytest.fixture
def store(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "cv.pdf").write_bytes(b"reviewed CV")
    (private / "success.png").write_bytes(b"saved fixture proof")
    return ApplicationStore(tmp_path / "workflow.sqlite3", private)


def approve(store, content=None):
    content = content or package()
    store.save_package(content, "cv.pdf")
    store.approve(content.application_id, content.tracker, content.fingerprint(), "owner-test")
    return content


def state(store):
    with sqlite3.connect(store.database_path) as db:
        return db.execute("SELECT status FROM applications ORDER BY id").fetchall()


def test_versions_immutability_and_stale_approval(store):
    first = approve(store)
    second = first.model_copy(update={"cv_version": "v2"})
    assert store.save_package(second, "cv.pdf").version == 2
    assert state(store) == [("review_ready",)]
    for operation in [
        lambda: store.approve(first.application_id, first.tracker, first.fingerprint(), "owner"),
        lambda: store.begin_attempt(
            first.application_id, first.tracker, first.fingerprint(), "old"
        ),
        lambda: store.save_package(first, "cv.pdf"),
    ]:
        with pytest.raises(ApplicationConflict, match="stale"):
            operation()
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM package_versions").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM application_approvals").fetchone()[0] == 1


def test_restart_idempotent_package_and_existing_queue_coexist(store):
    queued = enqueue_task(QueuedTaskKind.SCOUT_TODAY, TrackerName.TEST, path=store.database_path)
    content = approve(store)
    reopened = ApplicationStore(store.database_path, store.private_root)
    assert reopened.get_current(content.application_id, content.tracker).package == content
    assert reopened.save_package(content, "cv.pdf").version == 1
    reopened.approve(content.application_id, content.tracker, content.fingerprint(), "owner-test")
    assert state(reopened) == [("approved",)]
    assert [task.task_id for task in list_tasks(store.database_path)] == [queued.task_id]
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM application_schema_versions").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM application_events").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0] == 2


def test_v1_upgrade_preserves_authority_and_attempts_without_replaying(store):
    content = approve(store)
    attempt = store.begin_attempt(content.application_id, content.tracker, content.fingerprint(), "upgrade")
    with sqlite3.connect(store.database_path) as db:
        db.execute("DROP TABLE application_metadata")
        db.execute("DROP TABLE application_commands")
        db.execute("DROP TABLE application_routes")
        db.execute("DROP TABLE browser_observations")
        db.execute("DELETE FROM application_schema_versions WHERE version>=2")
        for column in ("claim_token", "lease_until", "next_attempt_at", "last_error"):
            db.execute(f"ALTER TABLE projection_outbox DROP COLUMN {column}")
    upgraded = ApplicationStore(store.database_path, store.private_root)
    assert is_ledger_owned(upgraded.database_path, content.application_id, content.tracker)
    assert upgraded.get_application(content.application_id, content.tracker).status == "sending"
    assert not upgraded.begin_attempt(content.application_id, content.tracker, content.fingerprint(), "upgrade").created
    assert upgraded.attempt_count(content.application_id, content.tracker) == 1
    assert attempt.state == "sending"


def test_test_and_production_never_share_approval(store):
    content = approve(store)
    production = content.model_copy(update={"tracker": TrackerName.PRODUCTION})
    store.save_package(production, "cv.pdf")
    with pytest.raises(ApplicationConflict, match="not approved"):
        store.begin_attempt(
            production.application_id, production.tracker, production.fingerprint(), "prod"
        )
    assert state(store) == [("approved",), ("review_ready",)]


def test_changed_file_rejected_before_approval_and_again_before_delivery(store):
    content = package()
    store.save_package(content, "cv.pdf")
    path = store.private_root / "cv.pdf"
    path.write_bytes(b"changed CV")
    with pytest.raises(ApplicationConflict, match="checksum"):
        store.approve(content.application_id, content.tracker, content.fingerprint(), "owner")
    path.write_bytes(b"reviewed CV")
    store.approve(content.application_id, content.tracker, content.fingerprint(), "owner")
    path.write_bytes(b"changed again")
    with pytest.raises(ApplicationConflict, match="checksum"):
        store.begin_attempt(content.application_id, content.tracker, content.fingerprint(), "send")
    assert state(store) == [("approved",)]


@pytest.mark.parametrize("path", ["../outside.pdf", "/etc/passwd", "missing.pdf", ""])
def test_invalid_artifact_paths_rejected_without_records(store, path):
    with pytest.raises(ValueError):
        store.save_package(package(), path)
    assert state(store) == []


def test_symlink_to_external_artifact_is_rejected(store, tmp_path):
    external = tmp_path / "outside.pdf"
    external.write_bytes(b"reviewed CV")
    (store.private_root / "linked.pdf").symlink_to(external)
    with pytest.raises(ValueError, match="escapes"):
        store.save_package(package(), "linked.pdf")


def test_one_concurrent_attempt_wins_and_crash_cannot_replay(store):
    content = approve(store)

    def begin(_):
        return store.begin_attempt(
            content.application_id, content.tracker, content.fingerprint(), "stable-key"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(begin, range(2)))
    assert sum(attempt.created for attempt in attempts) == 1
    assert attempts[0].attempt_id == attempts[1].attempt_id
    restarted = ApplicationStore(store.database_path, store.private_root)
    replay = restarted.begin_attempt(
        content.application_id, content.tracker, content.fingerprint(), "stable-key"
    )
    assert not replay.created
    assert replay.state == "sending"
    with pytest.raises(ApplicationConflict, match="unresolved"):
        restarted.begin_attempt(
            content.application_id, content.tracker, content.fingerprint(), "new-key"
        )


def test_unknown_requires_reconciliation_and_portal_requires_proof(store):
    content = approve(store)
    attempt = store.begin_attempt(
        content.application_id, content.tracker, content.fingerprint(), "first"
    )
    store.finish_attempt(attempt.attempt_id, "unknown", "Browser lost after click")
    assert state(store) == [("submission_unknown",)]
    with pytest.raises(ApplicationConflict):
        store.begin_attempt(
            content.application_id, content.tracker, content.fingerprint(), "second"
        )
    with pytest.raises(ApplicationConflict, match="screenshot"):
        store.finish_attempt(attempt.attempt_id, "submitted", "Confirmed page")
    store.finish_attempt(attempt.attempt_id, "submitted", "Confirmed page", "success.png")
    assert state(store) == [("submitted",)]
    store.finish_attempt(attempt.attempt_id, "submitted", "Confirmed page", "success.png")
    with pytest.raises(ApplicationConflict, match="overwritten"):
        store.finish_attempt(attempt.attempt_id, "not_sent", "Changed history")
    with sqlite3.connect(store.database_path) as db:
        proof_hash = db.execute("SELECT proof_sha256 FROM delivery_attempts").fetchone()[0]
    assert proof_hash == hashlib.sha256(b"saved fixture proof").hexdigest()


def test_only_confirmed_not_sent_allows_new_request(store):
    content = approve(store)
    first = store.begin_attempt(
        content.application_id, content.tracker, content.fingerprint(), "first"
    )
    store.finish_attempt(
        first.attempt_id, "not_sent", "Adapter confirmed no request was transmitted"
    )
    assert state(store) == [("approved",)]
    replay = store.begin_attempt(
        content.application_id, content.tracker, content.fingerprint(), "first"
    )
    assert not replay.created and replay.state == "not_sent"
    next_attempt = store.begin_attempt(
        content.application_id, content.tracker, content.fingerprint(), "second"
    )
    assert next_attempt.created


def test_cross_job_request_key_conflict_and_manual_whatsapp(store):
    content = approve(store)
    store.begin_attempt(content.application_id, content.tracker, content.fingerprint(), "shared")
    other = approve(store, package(application_id="different-job"))
    with pytest.raises(ApplicationConflict, match="another package"):
        store.begin_attempt(other.application_id, other.tracker, other.fingerprint(), "shared")
    handoff = approve(store, package(application_id="manual-job", delivery_route="whatsapp"))
    with pytest.raises(ApplicationConflict, match="user-sent"):
        store.begin_attempt(
            handoff.application_id, handoff.tracker, handoff.fingerprint(), "manual"
        )


def test_event_failure_rolls_back_application_package_and_outbox(store, monkeypatch):
    def fail(*_):
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="outbox"):
        store.save_package(package(), "cv.pdf")
    assert state(store) == []
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM package_versions").fetchone()[0] == 0


def test_consistent_backup_can_be_restored_and_future_schema_is_rejected(store, tmp_path):
    content = approve(store)
    copy_path = tmp_path / "copy.sqlite3"
    with sqlite3.connect(store.database_path) as source, sqlite3.connect(copy_path) as target:
        source.backup(target)
    copied = ApplicationStore(copy_path, store.private_root)
    assert copied.get_current(content.application_id, content.tracker).package == content
    with sqlite3.connect(copy_path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("UPDATE application_schema_versions SET version=99 WHERE version=3")
    with pytest.raises(ApplicationConflict, match="unsupported"):
        ApplicationStore(copy_path, store.private_root)


def test_discovery_is_stored_before_cv_and_review_protects_job_changes(store):
    job = Job(
        company="Example",
        title="Backend Engineer",
        description="Build a backend API",
        source="official",
        source_url="https://jobs.example.test/123",
        application_url="https://jobs.example.test/123",
    )
    store.register_application("example-job", TrackerName.TEST, job)
    discovered = store.get_application("example-job", TrackerName.TEST)
    assert discovered.status == "discovered"
    assert discovered.current_fingerprint is None
    assert discovered.job == job
    store.register_application("example-job", TrackerName.TEST, job)
    approve(store)
    store.register_application(
        "example-job", TrackerName.TEST, job.model_copy(update={"match_score": 90})
    )
    with pytest.raises(ApplicationConflict, match="facts changed"):
        store.register_application(
            "example-job",
            TrackerName.TEST,
            job.model_copy(update={"description": "Changed requirements"}),
        )
    assert store.get_application("example-job", TrackerName.TEST).status == "approved"


def test_package_cannot_replace_discovered_company_or_official_url(store):
    job = Job(
        company="Example",
        title="Backend Engineer",
        description="Build an API",
        source="official",
        source_url="https://jobs.example.test/123",
        application_url="https://jobs.example.test/123",
    )
    store.register_application("example-job", TrackerName.TEST, job)
    with pytest.raises(ApplicationConflict, match="identity or destination"):
        store.save_package(package(company="Other Company"), "cv.pdf")
    with pytest.raises(ApplicationConflict, match="identity or destination"):
        store.save_package(package(official_destination="https://other.example.test/123"), "cv.pdf")
