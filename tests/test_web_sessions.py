import base64
import hashlib
import secrets
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from backend_scout.web_sessions import (
    MAX_PAIRING_ATTEMPTS,
    PAIRING_TTL_SECONDS,
    PAIRING_WINDOW_SECONDS,
    SESSION_TTL_SECONDS,
    OwnerSession,
    OwnerSessionStore,
    PairingRejected,
)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return OwnerSessionStore(tmp_path / "workflow.sqlite3", {"owner"}, clock)


def pair(store, client="browser"):
    return store.redeem_pairing_code(store.issue_pairing_code("owner"), client)


def row_count(store, table):
    with sqlite3.connect(store.db_path) as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_pairing_authentication_and_revocation_survive_restart(store, clock):
    code = store.issue_pairing_code("owner")
    restarted = OwnerSessionStore(store.db_path, {"owner"}, clock)
    credentials = restarted.redeem_pairing_code(code, "browser")
    assert credentials.actor_id == "owner"
    assert credentials.expires_at == clock.now + timedelta(seconds=SESSION_TTL_SECONDS)
    assert len(base64.urlsafe_b64decode(credentials.token + "=")) == 32
    assert len(base64.urlsafe_b64decode(credentials.csrf_token + "=")) == 32
    assert len(base64.urlsafe_b64decode(code + "=")) == 32
    assert len({code, credentials.token, credentials.csrf_token}) == 3
    session = OwnerSession("owner", credentials.expires_at)
    assert restarted.authenticate(credentials.token) == session
    assert not hasattr(session, "token")
    assert restarted.verify_csrf(credentials.token, credentials.csrf_token)
    restarted = OwnerSessionStore(store.db_path, {"owner"}, clock)
    assert restarted.authenticate(credentials.token) == session
    assert restarted.verify_csrf(credentials.token, credentials.csrf_token)
    restarted.revoke(credentials.token)
    restarted.revoke(credentials.token)
    assert store.authenticate(credentials.token) is None
    assert not store.verify_csrf(credentials.token, credentials.csrf_token)
    with pytest.raises(PairingRejected, match="^Pairing request rejected$"):
        restarted.redeem_pairing_code(code, "different-browser")


@pytest.mark.parametrize("actor", ["intruder", "", " owner", "OWNER", None, ["owner"]])
def test_only_exact_allowlisted_actor_can_issue(store, actor):
    with pytest.raises(PermissionError, match="^Owner identity is not authorized$"):
        store.issue_pairing_code(actor)
    assert row_count(store, "owner_auth_pairing_codes") == 0


def test_allowlist_is_snapshotted_and_rechecked_on_restart(tmp_path, clock):
    actors = {"owner"}
    store = OwnerSessionStore(tmp_path / "workflow.sqlite3", actors, clock)
    actors.add("intruder")
    with pytest.raises(PermissionError):
        store.issue_pairing_code("intruder")
    credentials = pair(store)
    code = store.issue_pairing_code("owner")
    restricted = OwnerSessionStore(store.db_path, set(), clock)
    assert restricted.authenticate(credentials.token) is None
    assert not restricted.verify_csrf(credentials.token, credentials.csrf_token)
    with pytest.raises(PairingRejected):
        restricted.redeem_pairing_code(code, "browser")


def test_code_rotation_and_exact_expiry(store, clock):
    old = store.issue_pairing_code("owner")
    current = store.issue_pairing_code("owner")
    assert current != old
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(old, "browser")
    clock.advance(PAIRING_TTL_SECONDS - 1)
    assert store.redeem_pairing_code(current, "browser").actor_id == "owner"
    code = store.issue_pairing_code("owner")
    clock.advance(PAIRING_TTL_SECONDS)
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(code, "browser")
    assert row_count(store, "owner_auth_sessions") == 1


def test_session_absolute_expiry_and_csrf_expiry(store, clock):
    credentials = pair(store)
    clock.advance(SESSION_TTL_SECONDS - 1)
    assert store.authenticate(credentials.token) is not None
    assert store.verify_csrf(credentials.token, credentials.csrf_token)
    clock.advance(1)
    assert store.authenticate(credentials.token) is None
    assert not store.verify_csrf(credentials.token, credentials.csrf_token)


def test_csrf_is_bound_to_session_and_tokens_have_separate_domains(store):
    first = pair(store)
    second = pair(store)
    assert not store.verify_csrf(first.token, second.csrf_token)
    assert not store.verify_csrf(second.token, first.csrf_token)
    assert not store.verify_csrf(first.token, first.token)
    assert not store.verify_csrf(secrets.token_urlsafe(32), first.csrf_token)
    assert store.authenticate(first.csrf_token) is None
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(first.token, "browser")
    store.revoke(first.token)
    assert store.authenticate(second.token) is not None
    assert store.verify_csrf(second.token, second.csrf_token)


@pytest.mark.parametrize("invalid", [None, 42, [], "", "x" * 42, "x" * 44, "!" * 43, "é" * 43])
def test_malformed_secrets_fail_closed(store, invalid):
    credentials = pair(store)
    assert store.authenticate(invalid) is None
    assert not store.verify_csrf(credentials.token, invalid)
    assert not store.verify_csrf(invalid, credentials.csrf_token)
    store.revoke(invalid)
    with pytest.raises(PairingRejected, match="^Pairing request rejected$"):
        store.redeem_pairing_code(invalid, "browser")
    assert store.authenticate(credentials.token) is not None


def test_failed_attempts_commit_persist_and_limit_only_their_client(store, clock):
    for _ in range(MAX_PAIRING_ATTEMPTS):
        with pytest.raises(PairingRejected):
            store.redeem_pairing_code(secrets.token_urlsafe(32), "limited-client")
    restarted = OwnerSessionStore(store.db_path, {"owner"}, clock)
    code = restarted.issue_pairing_code("owner")
    with pytest.raises(PairingRejected):
        restarted.redeem_pairing_code(code, "limited-client")
    # A rate-limited valid request must not consume the owner's code.
    assert restarted.redeem_pairing_code(code, "other-client").actor_id == "owner"
    clock.advance(PAIRING_WINDOW_SECONDS - 1)
    code = restarted.issue_pairing_code("owner")
    with pytest.raises(PairingRejected):
        restarted.redeem_pairing_code(code, "limited-client")
    clock.advance(1)
    assert restarted.redeem_pairing_code(code, "limited-client").actor_id == "owner"


def test_successes_and_malformed_codes_also_use_attempt_budget(store, clock):
    pair(store, "limited-client")
    for _ in range(MAX_PAIRING_ATTEMPTS - 1):
        with pytest.raises(PairingRejected):
            store.redeem_pairing_code("not-a-code", "limited-client")
    code = store.issue_pairing_code("owner")
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(code, "limited-client")
    clock.advance(PAIRING_WINDOW_SECONDS)
    code = store.issue_pairing_code("owner")
    assert store.redeem_pairing_code(code, "limited-client").actor_id == "owner"


@pytest.mark.parametrize("client", [None, "", [], 42, "x" * 1025])
def test_invalid_client_key_never_consumes_code(store, client):
    code = store.issue_pairing_code("owner")
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(code, client)
    assert store.redeem_pairing_code(code, "browser").actor_id == "owner"


def test_concurrent_redemption_has_exactly_one_winner(store, clock):
    code = store.issue_pairing_code("owner")
    stores = [OwnerSessionStore(store.db_path, {"owner"}, clock) for _ in range(4)]
    barrier = Barrier(len(stores))

    def redeem(index):
        barrier.wait()
        try:
            return stores[index].redeem_pairing_code(code, f"client-{index}")
        except PairingRejected:
            return None

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        results = list(executor.map(redeem, range(len(stores))))
    assert sum(result is not None for result in results) == 1
    assert row_count(store, "owner_auth_sessions") == 1
    assert row_count(store, "owner_auth_pairing_codes") == 0


def test_concurrent_attempts_cannot_bypass_client_limit(store):
    barrier = Barrier(MAX_PAIRING_ATTEMPTS + 3)

    def redeem(_):
        barrier.wait()
        with pytest.raises(PairingRejected):
            store.redeem_pairing_code("invalid", "shared-client")

    with ThreadPoolExecutor(max_workers=barrier.parties) as executor:
        list(executor.map(redeem, range(barrier.parties)))
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT attempts FROM owner_auth_pairing_clients").fetchall() == [
            (MAX_PAIRING_ATTEMPTS,)
        ]
    code = store.issue_pairing_code("owner")
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(code, "shared-client")


def test_session_write_failure_rolls_back_code_consumption(store):
    code = store.issue_pairing_code("owner")
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "CREATE TRIGGER synthetic_failure BEFORE INSERT ON owner_auth_sessions "
            "BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic storage failure") as rejected:
        store.redeem_pairing_code(code, "browser")
    assert code not in str(rejected.value)
    assert row_count(store, "owner_auth_pairing_codes") == 1
    assert row_count(store, "owner_auth_sessions") == 0
    with sqlite3.connect(store.db_path) as db:
        db.execute("DROP TRIGGER synthetic_failure")
    assert store.redeem_pairing_code(code, "browser").actor_id == "owner"


def test_database_repr_errors_and_logs_do_not_expose_secrets(store, caplog):
    code = store.issue_pairing_code("owner")
    credentials = store.redeem_pairing_code(code, "private-client-key")
    other_code = store.issue_pairing_code("owner")
    with pytest.raises(PairingRejected) as rejected:
        store.redeem_pairing_code(code, "private-client-key")
    with sqlite3.connect(store.db_path) as db:
        dump = "\n".join(db.iterdump())
        hashes = db.execute("SELECT token_hash, csrf_hash FROM owner_auth_sessions").fetchone()
    assert hashes[0] == hashlib.sha256(b"session\0" + credentials.token.encode()).digest()
    assert (
        hashes[1]
        == hashlib.sha256(b"csrf\0" + hashes[0] + credentials.csrf_token.encode()).digest()
    )
    assert all(isinstance(digest, bytes) and len(digest) == 32 for digest in hashes)
    for secret in (
        code,
        other_code,
        credentials.token,
        credentials.csrf_token,
        "private-client-key",
    ):
        assert secret not in dump
        assert secret.encode() not in store.db_path.read_bytes()
        assert secret not in str(rejected.value)
        assert secret not in repr(credentials)
        assert secret not in caplog.text


def test_constant_time_digest_checks_are_used(store, monkeypatch):
    calls = []
    original = secrets.compare_digest

    def compare(left, right):
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(secrets, "compare_digest", compare)
    credentials = pair(store)
    assert len(calls) == 1
    assert store.authenticate(credentials.token)
    assert len(calls) == 2
    assert store.verify_csrf(credentials.token, credentials.csrf_token)
    assert len(calls) == 4
    assert store.authenticate(secrets.token_urlsafe(32)) is None
    assert len(calls) == 5
    with pytest.raises(PairingRejected):
        store.redeem_pairing_code(secrets.token_urlsafe(32), "browser")
    assert len(calls) == 6
    assert all(len(left) == len(right) == 32 for left, right in calls)


@pytest.mark.parametrize("auth_first", [True, False])
def test_auth_schema_coexists_with_queue_and_application_store(tmp_path, clock, auth_first):
    path = tmp_path / "workflow.sqlite3"
    if auth_first:
        store = OwnerSessionStore(path, {"owner"}, clock)
    # Synthetic unrelated operational rows must remain byte-for-byte unchanged.
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=7")
        db.execute("CREATE TABLE application_schema_versions (version INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO application_schema_versions VALUES (1)")
        db.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        db.execute("INSERT INTO applications VALUES (1, 'review_ready')")
        db.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
        db.execute("INSERT INTO tasks VALUES ('synthetic-task', '{\"tracker\":\"test\"}')")
        before = {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("application_schema_versions", "applications", "tasks")
        }
    if not auth_first:
        store = OwnerSessionStore(path, {"owner"}, clock)
    credentials = pair(store)
    restarted = OwnerSessionStore(path, {"owner"}, clock)
    assert restarted.authenticate(credentials.token).actor_id == "owner"
    with sqlite3.connect(path) as db:
        for table, rows in before.items():
            assert db.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert db.execute("SELECT version FROM owner_auth_schema_versions").fetchall() == [(1,)]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_actual_application_store_and_queue_share_authenticated_database(store, tmp_path, clock):
    from backend_scout.application_store import ApplicationStore
    from backend_scout.config import TrackerName
    from backend_scout.task_queue import QueuedTaskKind, enqueue_task, list_tasks

    credentials = pair(store)
    ApplicationStore(store.db_path, tmp_path / "private")
    task = enqueue_task(QueuedTaskKind.SCOUT_TODAY, TrackerName.TEST, path=store.db_path)
    restarted = OwnerSessionStore(store.db_path, {"owner"}, clock)
    assert restarted.authenticate(credentials.token).actor_id == "owner"
    assert restarted.verify_csrf(credentials.token, credentials.csrf_token)
    assert [queued.task_id for queued in list_tasks(store.db_path)] == [task.task_id]


def test_new_database_is_owner_only_and_existing_mode_is_preserved(tmp_path, clock):
    path = tmp_path / "new" / "workflow.sqlite3"
    OwnerSessionStore(path, {"owner"}, clock)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o640)
    OwnerSessionStore(path, {"owner"}, clock)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_future_auth_schema_rejected_without_touching_other_versions(store, clock):
    with sqlite3.connect(store.db_path) as db:
        db.execute("PRAGMA user_version=77")
        db.execute("UPDATE owner_auth_schema_versions SET version=99")
    with pytest.raises(ValueError, match="authentication schema is unsupported"):
        OwnerSessionStore(store.db_path, {"owner"}, clock)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 77
        assert db.execute("SELECT version FROM owner_auth_schema_versions").fetchone()[0] == 99


def test_unix_clock_and_default_clock_supported(tmp_path):
    store = OwnerSessionStore(tmp_path / "timestamp.sqlite3", {"owner"}, lambda: 1234.0)
    assert pair(store).expires_at.timestamp() == 1234.0 + SESSION_TTL_SECONDS
    default = OwnerSessionStore(tmp_path / "default.sqlite3", {"owner"})
    assert default.authenticate(pair(default).token).actor_id == "owner"
