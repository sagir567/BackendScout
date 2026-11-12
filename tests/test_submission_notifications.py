import base64
import hashlib
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from backend_scout import submission_notifications as notifications
from backend_scout.application_package import ApplicationPackage
from backend_scout.application_store import ApplicationStore
from backend_scout.config import Settings, TrackerName
from backend_scout.telegram import TelegramClient

CV = b"%PDF-1.4\nSynthetic approved CV"
PROOF = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def response(message_id=71, chat_id=12345):
    return {
        "ok": True,
        "result": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
        },
    }


class FakeTelegram:
    def __init__(self):
        self.photos = []
        self.tokens = []
        self.results = []
        self.on_send = None
        self.close_error = False

    def __call__(self, token):
        self.tokens.append(token)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.close_error:
            raise OSError("synthetic client close failure")

    def send_photo(self, chat_id, path, caption):
        self.photos.append((chat_id, path.read_bytes(), caption, path))
        if self.on_send:
            self.on_send()
        result = self.results.pop(0) if self.results else response()
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network calls are prohibited")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    (private / "cv.pdf").write_bytes(CV)
    (private / "success.png").write_bytes(PROOF)
    settings = Settings(
        _env_file=None,
        telegram_bot_token="synthetic-token",
        telegram_default_chat_id=12345,
        telegram_allowed_user_ids="12345",
        workflow_database_path=tmp_path / "workflow.sqlite3",
        cv_archive_root=private / "cv",
    )
    store = ApplicationStore(settings.workflow_database_path, private)
    with sqlite3.connect(store.database_path) as db:
        notifications.install_notification_schema(db)
    package = ApplicationPackage(
        application_id="example-job",
        tracker=TrackerName.TEST,
        company="Example Company",
        role="Backend Engineer",
        official_destination="https://jobs.example.test/123",
        delivery_route="portal",
        cv_version="reviewed-v3",
        cv_sha256=hashlib.sha256(CV).hexdigest(),
        answers=[{"key": "private answer", "value": "do not notify this answer"}],
    )
    store.save_package(package, "cv.pdf")
    store.approve(package.application_id, package.tracker, package.fingerprint(), "owner")
    attempt = store.begin_attempt(
        package.application_id, package.tracker, package.fingerprint(), "application-once"
    )
    store.finish_attempt(attempt.attempt_id, "submitted", "private receipt", "success.png")
    # Isolate these unit tests from the parent's optional automatic enqueue hook.
    with sqlite3.connect(store.database_path) as db:
        db.execute("DELETE FROM submission_notification_outbox")
    clock = SimpleNamespace(now=datetime(2026, 11, 4, 12, tzinfo=UTC))
    monkeypatch.setattr(notifications, "_now", lambda: clock.now)
    return SimpleNamespace(
        store=store,
        settings=settings,
        package=package,
        attempt=attempt,
        client=FakeTelegram(),
        clock=clock,
    )


def enqueue(fixture, **updates):
    values = {
        "application_id": fixture.package.application_id,
        "tracker": fixture.package.tracker,
        "fingerprint": fixture.package.fingerprint(),
    } | updates
    return notifications.enqueue_submission_notification(fixture.store.database_path, **values)


def dispatch(fixture, **kwargs):
    return notifications.dispatch_submission_notifications(
        fixture.store, fixture.settings, client_factory=fixture.client, **kwargs
    )


def rows(fixture):
    with sqlite3.connect(fixture.store.database_path) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute("SELECT * FROM submission_notification_outbox ORDER BY id")
        ]


def update(fixture, statement, values=()):
    with sqlite3.connect(fixture.store.database_path) as db:
        db.execute(statement, values)


def test_schema_installs_idempotently_without_committing_parent_transaction():
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE parent_change (value TEXT)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO parent_change VALUES ('uncommitted')")
        notifications.install_notification_schema(db)
        notifications.install_notification_schema(db)
        assert db.in_transaction
        db.rollback()
        assert db.execute("SELECT * FROM parent_change").fetchall() == []
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='submission_notification_outbox'"
        ).fetchall()


def test_enqueue_is_stable_across_concurrent_calls_and_restart(fixture):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: enqueue(fixture), range(8)))
    assert len(set(ids)) == 1
    fixture.store = ApplicationStore(fixture.store.database_path, fixture.store.private_root)
    assert enqueue(fixture) == ids[0]
    assert len(rows(fixture)) == 1
    assert rows(fixture)[0]["attempts"] == 0


def test_enqueue_participates_in_parent_transaction_and_rollback(fixture):
    with sqlite3.connect(fixture.store.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE delivery_attempts SET receipt='uncommitted receipt'")
        first = enqueue(fixture, connection=db)
        assert enqueue(fixture, connection=db) == first
        assert db.in_transaction
        assert rows(fixture) == []
        db.rollback()
        assert (
            db.execute("SELECT receipt FROM delivery_attempts").fetchone()[0] == "private receipt"
        )
    assert rows(fixture) == []
    with sqlite3.connect(fixture.store.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        enqueue(fixture, connection=db)
        db.commit()
    assert len(rows(fixture)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"application_id": " "},
        {"tracker": "other"},
        {"fingerprint": "old"},
        {"fingerprint": "A" * 64},
        {"fingerprint": None},
    ],
)
def test_invalid_enqueue_arguments_do_not_create_rows(fixture, changes):
    with pytest.raises(ValueError):
        enqueue(fixture, **changes)
    assert rows(fixture) == []


def test_success_records_ack_before_cleanup_and_only_notifies(fixture, monkeypatch):
    from backend_scout.application_service import ApplicationService
    from backend_scout.notion import NotionClient
    from backend_scout.package_portal import PackagePortalAdapter
    from backend_scout.portal_sessions import PortalSessionManager

    def forbidden(*args, **kwargs):
        pytest.fail("Notifications must not call broker/delivery/approval/Notion/browser cleanup")

    for method in ("begin_attempt", "finish_attempt", "approve"):
        monkeypatch.setattr(ApplicationStore, method, forbidden)
    for method in ("deliver", "queue_delivery", "approve_and_queue", "sync_projection"):
        monkeypatch.setattr(ApplicationService, method, forbidden)
    monkeypatch.setattr(PackagePortalAdapter, "__call__", forbidden)
    for method in ("acquire", "close", "close_session"):
        monkeypatch.setattr(PortalSessionManager, method, forbidden)
    monkeypatch.setattr(NotionClient, "__init__", forbidden)
    enqueue(fixture)
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []

    def during_send():
        assert rows(fixture)[0]["status"] == "processing"
        assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []

    fixture.client.on_send = during_send
    counts = dispatch(fixture)
    assert counts == notifications.NotificationDispatchCounts(claimed=1, acknowledged=1)
    row = rows(fixture)[0]
    assert (row["status"], row["message_id"], row["chat_id"]) == ("acknowledged", 71, 12345)
    assert fixture.client.photos[0][2].startswith("Test submission confirmation\n")
    assert row["acknowledged_at"] == fixture.clock.now.isoformat()
    assert row["claim_token"] is row["lease_until"] is row["next_attempt_at"] is None
    chat_id, proof, caption, snapshot = fixture.client.photos[0]
    assert chat_id == 12345 and proof == PROOF
    assert snapshot.is_relative_to(fixture.store.private_root) and not snapshot.exists()
    for text in (
        "Example Company",
        "Backend Engineer",
        "reviewed-v3",
        fixture.package.cv_sha256,
        fixture.package.fingerprint(),
        "test",
    ):
        assert text in caption
    for secret in (
        "private receipt",
        "private answer",
        "do not notify this answer",
        "synthetic-token",
    ):
        assert secret not in caption
    identities = [(fixture.package.application_id, TrackerName.TEST, fixture.package.fingerprint())]
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == identities
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == identities
    assert (fixture.store.private_root / "success.png").read_bytes() == PROOF
    assert fixture.store.attempt_count(fixture.package.application_id, TrackerName.TEST) == 1
    assert (
        fixture.store.get_application(fixture.package.application_id, TrackerName.TEST).status
        == "submitted"
    )
    before = rows(fixture)
    enqueue(fixture)
    assert dispatch(fixture).claimed == 0
    assert rows(fixture) == before
    assert len(fixture.client.photos) == 1


def test_leases_have_one_winner_and_restart_recovers_expiration(fixture):
    enqueue(fixture)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(
            pool.map(lambda _: notifications._claim_next(fixture.store.database_path), range(4))
        )
    (winner,) = [claim for claim in claims if claim is not None]
    assert dispatch(fixture).claimed == 0
    fixture.clock.now += timedelta(seconds=notifications.LEASE_SECONDS + 1)
    fixture.store = ApplicationStore(fixture.store.database_path, fixture.store.private_root)
    replacement = notifications._claim_next(fixture.store.database_path)
    assert replacement is not None and replacement.token != winner.token
    assert replacement.attempts == 2
    assert not notifications._acknowledge(fixture.store.database_path, winner, 12345, 100)
    notifications._fail(fixture.store.database_path, winner, "obsolete worker")
    with pytest.raises(ValueError, match="notification_lease_lost"):
        notifications._renew(fixture.store.database_path, winner)
    assert rows(fixture)[0]["claim_token"] == replacement.token
    fixture.clock.now += timedelta(seconds=notifications.LEASE_SECONDS + 1)
    assert dispatch(fixture).acknowledged == 1
    assert rows(fixture)[0]["attempts"] == 3


def test_timeout_retries_only_notification_after_restart(fixture):
    enqueue(fixture)
    fixture.client.results = [TimeoutError("synthetic-token private receipt transport failure")]
    assert dispatch(fixture).retrying == 1
    first = rows(fixture)[0]
    assert first["status"] == "pending" and first["message_id"] is None
    assert first["last_error"] == "notification_send_failed"
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []
    assert dispatch(fixture).claimed == 0
    assert enqueue(fixture) == first["id"] and rows(fixture)[0] == first
    fixture.clock.now += timedelta(seconds=notifications.RETRY_SECONDS)
    fixture.store = ApplicationStore(fixture.store.database_path, fixture.store.private_root)
    assert dispatch(fixture).acknowledged == 1
    assert len(fixture.client.photos) == 2
    assert rows(fixture)[0]["attempts"] == 2
    assert fixture.store.attempt_count(fixture.package.application_id, TrackerName.TEST) == 1


def test_retry_backoff_is_bounded_but_transient_failures_are_not_discarded(fixture):
    enqueue(fixture)
    for _ in range(10):
        fixture.client.results = [OSError("synthetic failure")]
        assert dispatch(fixture).retrying == 1
        row = rows(fixture)[0]
        available = datetime.fromisoformat(row["next_attempt_at"])
        assert (
            0 < (available - fixture.clock.now).total_seconds() <= notifications.MAX_RETRY_SECONDS
        )
        fixture.clock.now = available
    assert row["attempts"] == 10 and row["status"] == "pending"
    assert dispatch(fixture).acknowledged == 1


def test_lost_ack_can_duplicate_photo_but_never_an_application(fixture, monkeypatch):
    enqueue(fixture)
    acknowledge = notifications._acknowledge
    monkeypatch.setattr(notifications, "_acknowledge", lambda *args: False)
    assert dispatch(fixture).retrying == 1
    assert rows(fixture)[0]["last_error"] == "notification_lease_lost"
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []
    monkeypatch.setattr(notifications, "_acknowledge", acknowledge)
    fixture.clock.now += timedelta(seconds=notifications.RETRY_SECONDS)
    assert dispatch(fixture).acknowledged == 1
    assert len(fixture.client.photos) == 2
    assert fixture.store.attempt_count(fixture.package.application_id, TrackerName.TEST) == 1


def test_expired_worker_cannot_send_or_acknowledge(fixture):
    enqueue(fixture)

    def delayed_factory(token):
        fixture.clock.now += timedelta(seconds=notifications.LEASE_SECONDS + 1)
        return fixture.client(token)

    counts = notifications.dispatch_submission_notifications(
        fixture.store, fixture.settings, client_factory=delayed_factory
    )
    assert counts.retrying == 1 and fixture.client.photos == []
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []


def test_client_close_error_does_not_undo_durable_ack(fixture):
    enqueue(fixture)
    fixture.client.close_error = True
    assert dispatch(fixture) == notifications.NotificationDispatchCounts(claimed=1, acknowledged=1)
    assert rows(fixture)[0]["status"] == "acknowledged"
    assert dispatch(fixture).claimed == 0


@pytest.mark.parametrize(
    "reply",
    [
        None,
        {},
        {"ok": False},
        {"ok": True, "result": {}},
        response(message_id=True),
        response(message_id=0),
        response(chat_id=99999),
        {"ok": True, "result": {"message_id": 71, "chat": {"id": 12345, "type": "group"}}},
    ],
)
def test_missing_or_untrusted_telegram_ack_never_allows_cleanup(fixture, reply):
    enqueue(fixture)
    fixture.client.results = [reply]
    assert dispatch(fixture).retrying == 1
    assert rows(fixture)[0]["last_error"] == "notification_ack_invalid"
    assert rows(fixture)[0]["message_id"] is None
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []


@pytest.mark.parametrize(
    "damage",
    [
        "cv_bytes",
        "proof_bytes",
        "cv_missing",
        "proof_missing",
        "cv_hash",
        "proof_hash",
        "absolute",
        "traversal",
        "symlink",
        "cv_symlink",
        "non_image",
        "non_pdf",
        "no_hash",
    ],
)
def test_tampered_hashes_and_paths_are_rejected_without_telegram(fixture, tmp_path, damage):
    enqueue(fixture)
    if damage in {"cv_bytes", "proof_bytes", "cv_missing", "proof_missing"}:
        path = fixture.store.private_root / ("cv.pdf" if damage.startswith("cv") else "success.png")
        path.unlink()
        if damage.endswith("bytes"):
            path.write_bytes(b"tampered")
    elif damage in {"cv_hash", "proof_hash", "no_hash"}:
        column, table = (
            ("sha256", "application_artifacts")
            if damage == "cv_hash"
            else ("proof_sha256", "delivery_attempts")
        )
        update(
            fixture, f"UPDATE {table} SET {column}=?", (None if damage == "no_hash" else "0" * 64,)
        )
    elif damage in {"symlink", "cv_symlink"}:
        outside = tmp_path / "outside"
        outside.write_bytes(CV if damage == "cv_symlink" else PROOF)
        path = fixture.store.private_root / ("cv.pdf" if damage == "cv_symlink" else "success.png")
        path.unlink()
        path.symlink_to(outside)
    elif damage == "non_pdf":
        (fixture.store.private_root / "cv.txt").write_bytes(CV)
        update(fixture, "UPDATE application_artifacts SET path='cv.txt'")
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(PROOF)
        path = {"absolute": str(outside), "traversal": "../outside.png", "non_image": "cv.pdf"}[
            damage
        ]
        update(fixture, "UPDATE delivery_attempts SET proof_path=?", (path,))
    assert dispatch(fixture).rejected == 1
    row = rows(fixture)[0]
    assert row["status"] == "rejected" and row["last_error"].startswith("notification_")
    assert fixture.client.tokens == [] and fixture.client.photos == []
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []
    assert enqueue(fixture) == row["id"] and rows(fixture)[0] == row
    assert dispatch(fixture).claimed == 0


@pytest.mark.parametrize(
    "damage",
    [
        "not_submitted",
        "old_fingerprint",
        "payload_identity",
        "payload_tracker",
        "no_approval",
        "not_submitted_delivery",
        "old_delivery_package",
        "empty_receipt",
        "artifact_owner",
        "newer_non_submitted_delivery",
    ],
)
def test_only_canonical_current_approved_submitted_package_is_notified(fixture, damage):
    enqueue(
        fixture,
        fingerprint="0" * 64 if damage == "old_fingerprint" else fixture.package.fingerprint(),
    )
    if damage == "not_submitted":
        update(fixture, "UPDATE applications SET status='submission_unknown'")
    elif damage == "payload_identity":
        package = fixture.package.model_copy(update={"application_id": "other-job"})
        update(fixture, "UPDATE package_versions SET payload_json=?", (package.canonical_json(),))
    elif damage == "payload_tracker":
        package = fixture.package.model_copy(update={"tracker": TrackerName.PRODUCTION})
        update(fixture, "UPDATE package_versions SET payload_json=?", (package.canonical_json(),))
    elif damage == "no_approval":
        update(fixture, "DELETE FROM application_approvals")
    elif damage == "not_submitted_delivery":
        update(fixture, "UPDATE delivery_attempts SET state='unknown'")
    elif damage == "old_delivery_package":
        update(fixture, "UPDATE delivery_attempts SET package_id=-1")
    elif damage == "empty_receipt":
        update(fixture, "UPDATE delivery_attempts SET receipt=' '")
    elif damage == "artifact_owner":
        update(fixture, "UPDATE application_artifacts SET application_pk=-1")
    elif damage == "newer_non_submitted_delivery":
        update(
            fixture,
            """INSERT INTO delivery_attempts
            (attempt_id,application_pk,package_id,request_key,state,created_at,updated_at)
            SELECT 'later',application_pk,package_id,'later','not_sent','9999','9999'
            FROM delivery_attempts""",
        )
    assert dispatch(fixture).rejected == 1
    assert fixture.client.tokens == []
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []


def test_tracker_mismatch_cannot_borrow_test_submission(fixture):
    update(
        fixture,
        "INSERT INTO applications(application_id,tracker,status) VALUES (?,'production','submitted')",
        (fixture.package.application_id,),
    )
    enqueue(fixture, tracker=TrackerName.PRODUCTION)
    assert dispatch(fixture).rejected == 1
    assert fixture.client.photos == []


@pytest.mark.parametrize(
    "settings",
    [
        {"telegram_bot_token": None},
        {"telegram_default_chat_id": 99999},
        {"telegram_default_chat_id": -12345, "telegram_allowed_user_ids": "-12345"},
        {"telegram_allowed_user_ids": None},
        {"telegram_allowed_user_ids": "invalid"},
        {"telegram_default_chat_id": None, "telegram_allowed_user_ids": "12345,67890"},
    ],
)
def test_unauthorized_or_ambiguous_destination_never_constructs_client(fixture, settings):
    enqueue(fixture)
    fixture.settings = Settings(_env_file=None, **(fixture.settings.model_dump() | settings))
    assert dispatch(fixture).retrying == 1
    assert fixture.client.tokens == [] and rows(fixture)[0]["status"] == "pending"
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []


def test_single_allowlisted_owner_is_safe_default(fixture):
    enqueue(fixture)
    fixture.settings.telegram_default_chat_id = None
    assert dispatch(fixture).acknowledged == 1
    assert fixture.client.photos[0][0] == 12345


def test_upload_uses_verified_snapshot_not_later_source_bytes(fixture):
    enqueue(fixture)

    def factory(token):
        (fixture.store.private_root / "success.png").write_bytes(b"changed after verification")
        return fixture.client(token)

    result = notifications.dispatch_submission_notifications(
        fixture.store, fixture.settings, client_factory=factory
    )
    assert result.acknowledged == 1
    assert fixture.client.photos[0][1] == PROOF


def test_existing_telegram_client_multipart_and_response_are_supported(fixture):
    enqueue(fixture)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/botsynthetic-token/sendPhoto"
        assert PROOF in request.content and b"reviewed-v3" in request.content
        return httpx.Response(200, json=response(message_id=817))

    def factory(token):
        client = TelegramClient.__new__(TelegramClient)
        client._client = httpx.Client(
            base_url=f"https://telegram.example.test/bot{token}",
            transport=httpx.MockTransport(handler),
        )
        return client

    counts = notifications.dispatch_submission_notifications(
        fixture.store, fixture.settings, client_factory=factory
    )
    assert counts.acknowledged == 1 and len(requests) == 1
    assert rows(fixture)[0]["message_id"] == 817


@pytest.mark.parametrize("limit", [-1, True, 1.5, 101])
def test_invalid_batch_limit_fails_without_claiming(fixture, limit):
    enqueue(fixture)
    with pytest.raises(ValueError, match="max_items"):
        dispatch(fixture, max_items=limit)
    assert rows(fixture)[0]["attempts"] == 0


def test_zero_limit_does_not_claim(fixture):
    enqueue(fixture)
    assert dispatch(fixture, max_items=0) == notifications.NotificationDispatchCounts()
    assert rows(fixture)[0]["attempts"] == 0


def test_schema_prevents_ack_without_message_and_unknown_status(fixture):
    enqueue(fixture)
    for statement in (
        "UPDATE submission_notification_outbox SET status='acknowledged'",
        "UPDATE submission_notification_outbox SET status='sent_maybe'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            update(fixture, statement)
    assert rows(fixture)[0]["status"] == "pending"


def test_enqueue_cannot_reference_an_application_in_another_tracker(fixture):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        enqueue(fixture, tracker=TrackerName.PRODUCTION)
    assert rows(fixture) == []


def test_concurrent_dispatch_sends_one_photo(fixture):
    enqueue(fixture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(fixture), range(2)))
    assert sum(result.claimed for result in results) == 1
    assert sum(result.acknowledged for result in results) == 1
    assert len(fixture.client.photos) == 1


def test_batch_limit_does_not_process_remaining_rows(fixture):
    enqueue(fixture, fingerprint="0" * 64)
    enqueue(fixture, fingerprint="1" * 64)
    enqueue(fixture)
    assert dispatch(fixture, max_items=2) == notifications.NotificationDispatchCounts(
        claimed=2, rejected=2
    )
    assert [row["status"] for row in rows(fixture)] == ["rejected", "rejected", "pending"]
    assert fixture.client.photos == []
    assert dispatch(fixture).acknowledged == 1


def test_crash_after_photo_before_ack_is_recovered_without_redelivery(fixture, monkeypatch):
    enqueue(fixture)
    acknowledge = notifications._acknowledge

    def crash(*args):
        raise KeyboardInterrupt("synthetic process interruption")

    monkeypatch.setattr(notifications, "_acknowledge", crash)
    with pytest.raises(KeyboardInterrupt):
        dispatch(fixture)
    assert rows(fixture)[0]["status"] == "processing"
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == []
    assert dispatch(fixture).claimed == 0
    monkeypatch.setattr(notifications, "_acknowledge", acknowledge)
    fixture.clock.now += timedelta(seconds=notifications.LEASE_SECONDS + 1)
    fixture.store = ApplicationStore(fixture.store.database_path, fixture.store.private_root)
    assert dispatch(fixture).acknowledged == 1
    assert len(fixture.client.photos) == 2
    assert fixture.store.attempt_count(fixture.package.application_id, TrackerName.TEST) == 1


def test_destination_configuration_can_be_repaired_without_reenqueue(fixture):
    enqueue(fixture)
    fixture.settings.telegram_default_chat_id = 99999
    assert dispatch(fixture).retrying == 1
    assert fixture.client.photos == []
    fixture.settings.telegram_default_chat_id = 12345
    fixture.clock.now += timedelta(seconds=notifications.RETRY_SECONDS)
    assert dispatch(fixture).acknowledged == 1


def test_acknowledgement_survives_process_restart_without_another_photo(fixture):
    enqueue(fixture)
    assert dispatch(fixture).acknowledged == 1
    fixture.store = ApplicationStore(fixture.store.database_path, fixture.store.private_root)
    before = rows(fixture)
    enqueue(fixture)
    assert dispatch(fixture).claimed == 0
    assert rows(fixture) == before
    assert len(fixture.client.photos) == 1
    assert notifications.acknowledged_submission_sessions(fixture.store.database_path) == [
        (fixture.package.application_id, TrackerName.TEST, fixture.package.fingerprint())
    ]


def test_valid_test_and_production_submissions_have_separate_notifications(fixture):
    production = fixture.package.model_copy(update={"tracker": TrackerName.PRODUCTION})
    store = fixture.store
    store.save_package(production, "cv.pdf")
    store.approve(production.application_id, production.tracker, production.fingerprint(), "owner")
    attempt = store.begin_attempt(
        production.application_id, production.tracker, production.fingerprint(), "production-once"
    )
    store.finish_attempt(attempt.attempt_id, "submitted", "production receipt", "success.png")
    enqueue(fixture)
    enqueue(fixture, tracker=production.tracker, fingerprint=production.fingerprint())
    assert dispatch(fixture).acknowledged == 2
    assert len(fixture.client.photos) == 2
    assert {
        identity[1]
        for identity in notifications.acknowledged_submission_sessions(store.database_path)
    } == {
        TrackerName.TEST,
        TrackerName.PRODUCTION,
    }
    assert {photo[2].splitlines()[-1] for photo in fixture.client.photos} == {
        "Tracker: test",
        "Tracker: production",
    }
