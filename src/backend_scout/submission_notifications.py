"""Durable, at-least-once Telegram proof notifications, never application delivery.

The store migration calls ``install_notification_schema(db)``. In finish_attempt's
transaction, enqueue with ``connection=db`` after recording submitted evidence;
enqueue neither commits nor closes a supplied connection. Dispatch is independent
of delivery, approvals, browsers and Notion. It never removes original artifacts.

A lost Telegram acknowledgement can duplicate a photo, not an application. Only
durably acknowledged identities are returned for the browser owner to compare to
its session bindings and close on its own thread. Reading them is non-destructive.
"""

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING
from uuid import uuid4

from backend_scout.application_package import ApplicationPackage
from backend_scout.config import Settings, TrackerName
from backend_scout.telegram import TelegramClient

if TYPE_CHECKING:
    from backend_scout.application_store import ApplicationStore

LEASE_SECONDS = 300
RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 3600
MAX_BATCH_SIZE = 100


class NotificationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"


@dataclass(frozen=True)
class NotificationDispatchCounts:
    claimed: int = 0
    acknowledged: int = 0
    retrying: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class _Claim:
    id: int
    application_id: str
    tracker: TrackerName
    fingerprint: str
    token: str
    attempts: int


class _Rejected(ValueError):
    """Invalid canonical evidence; the reason is safe to persist."""


class _Retry(ValueError):
    """A safe, stable notification-only retry reason."""


def _now() -> datetime:
    return datetime.now(UTC)


def install_notification_schema(db: sqlite3.Connection) -> None:
    """Install within the parent's migration, without an implicit commit."""
    db.execute("""CREATE TABLE IF NOT EXISTS submission_notification_outbox (
        id INTEGER PRIMARY KEY,
        application_id TEXT NOT NULL,
        tracker TEXT NOT NULL CHECK(tracker IN ('test','production')),
        fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','processing','acknowledged','rejected')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
        claim_token TEXT,
        lease_until TEXT,
        next_attempt_at TEXT,
        last_error TEXT,
        chat_id INTEGER,
        message_id INTEGER,
        acknowledged_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(application_id,tracker,fingerprint),
        FOREIGN KEY(application_id,tracker) REFERENCES applications(application_id,tracker),
        CHECK(status!='acknowledged' OR
            (message_id IS NOT NULL AND message_id>0 AND chat_id IS NOT NULL
             AND chat_id>0 AND acknowledged_at IS NOT NULL))
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS submission_notifications_due
        ON submission_notification_outbox(status,next_attempt_at,lease_until,id)""")


@contextmanager
def _transaction(database_path: Path):
    path = Path(database_path).expanduser().resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def enqueue_submission_notification(
    database_path: Path,
    application_id: str,
    tracker: TrackerName,
    fingerprint: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    """Return the stable outbox ID; duplicates never reset status or retry state.

    The schema must already be installed. A supplied connection belongs entirely
    to the caller, including transaction boundaries and rollback on failure.
    """
    if not isinstance(application_id, str) or not application_id.strip():
        raise ValueError("Application identity is required")
    tracker = TrackerName(tracker)
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ValueError("A canonical package fingerprint is required")
    if connection is None:
        with _transaction(database_path) as db:
            return enqueue_submission_notification(
                database_path, application_id, tracker, fingerprint, connection=db
            )
    now = _now().isoformat()
    connection.execute(
        "INSERT INTO submission_notification_outbox "
        "(application_id,tracker,fingerprint,created_at,updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(application_id,tracker,fingerprint) DO NOTHING",
        (application_id, tracker.value, fingerprint, now, now),
    )
    return connection.execute(
        "SELECT id FROM submission_notification_outbox "
        "WHERE application_id=? AND tracker=? AND fingerprint=?",
        (application_id, tracker.value, fingerprint),
    ).fetchone()[0]


def _claim_next(database_path: Path) -> _Claim | None:
    now, token = _now(), uuid4().hex
    with _transaction(database_path) as db:
        row = db.execute(
            "SELECT * FROM submission_notification_outbox WHERE "
            "(status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)) OR "
            "(status='processing' AND (lease_until IS NULL OR lease_until<=?)) "
            "ORDER BY id LIMIT 1",
            (now.isoformat(), now.isoformat()),
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE submission_notification_outbox SET status='processing',attempts=attempts+1,"
            "claim_token=?,lease_until=?,next_attempt_at=NULL,updated_at=? WHERE id=?",
            (
                token,
                (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                now.isoformat(),
                row["id"],
            ),
        )
        return _Claim(
            row["id"],
            row["application_id"],
            TrackerName(row["tracker"]),
            row["fingerprint"],
            token,
            row["attempts"] + 1,
        )


def _renew(database_path: Path, claim: _Claim) -> None:
    now = _now()
    with _transaction(database_path) as db:
        changed = db.execute(
            "UPDATE submission_notification_outbox SET lease_until=?,updated_at=? "
            "WHERE id=? AND status='processing' AND claim_token=? AND lease_until>?",
            (
                (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                now.isoformat(),
                claim.id,
                claim.token,
                now.isoformat(),
            ),
        ).rowcount
        if changed != 1:
            raise _Retry("notification_lease_lost")


def _acknowledge(database_path: Path, claim: _Claim, chat_id: int, message_id: int) -> bool:
    now = _now().isoformat()
    with _transaction(database_path) as db:
        return (
            db.execute(
                "UPDATE submission_notification_outbox SET status='acknowledged',chat_id=?,"
                "message_id=?,acknowledged_at=?,updated_at=?,last_error=NULL,claim_token=NULL,"
                "lease_until=NULL,next_attempt_at=NULL WHERE id=? AND status='processing' "
                "AND claim_token=? AND lease_until>?",
                (chat_id, message_id, now, now, claim.id, claim.token, now),
            ).rowcount
            == 1
        )


def _fail(database_path: Path, claim: _Claim, code: str, *, rejected: bool = False) -> None:
    now = _now()
    delay = min(MAX_RETRY_SECONDS, RETRY_SECONDS * 2 ** min(claim.attempts - 1, 6))
    with _transaction(database_path) as db:
        db.execute(
            "UPDATE submission_notification_outbox SET status=?,last_error=?,next_attempt_at=?,"
            "claim_token=NULL,lease_until=NULL,updated_at=? "
            "WHERE id=? AND status='processing' AND claim_token=?",
            (
                "rejected" if rejected else "pending",
                code,
                None if rejected else (now + timedelta(seconds=delay)).isoformat(),
                now.isoformat(),
                claim.id,
                claim.token,
            ),
        )


def _owner_chat(settings: Settings) -> int:
    if not settings.telegram_bot_token or not settings.telegram_bot_token.strip():
        raise _Retry("notification_configuration_missing")
    try:
        allowed = settings.telegram_allowed_user_id_set
    except (TypeError, ValueError):
        raise _Retry("notification_destination_unauthorized") from None
    chat_id = settings.telegram_default_chat_id
    if chat_id is None and len(allowed) == 1:
        chat_id = next(iter(allowed))
    if type(chat_id) is not int or chat_id <= 0 or chat_id not in allowed:
        raise _Retry("notification_destination_unauthorized")
    return chat_id


def _verified_bytes(private_root: Path, relative: str, expected: str, suffixes: set[str]) -> bytes:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise _Rejected("notification_artifact_invalid")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in suffixes:
        raise _Rejected("notification_artifact_invalid")
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise _Rejected("notification_artifact_invalid")
    try:
        target = (private_root / path).resolve()
        if not target.is_relative_to(private_root) or not target.is_file():
            raise _Rejected("notification_artifact_invalid")
        content = target.read_bytes()
    except (OSError, ValueError, RuntimeError):
        raise _Rejected("notification_artifact_invalid") from None
    if hashlib.sha256(content).hexdigest() != expected:
        raise _Rejected("notification_artifact_hash_mismatch")
    return content


def _notification_content(store: "ApplicationStore", claim: _Claim) -> tuple[bytes, str, str]:
    with _transaction(store.database_path) as db:
        row = db.execute(
            "SELECT a.id AS application_pk,a.status,a.current_fingerprint,p.id AS package_id,"
            "p.payload_json,f.path AS cv_path,f.sha256 AS cv_hash,f.application_pk AS artifact_owner,"
            "f.kind,ap.id AS approval_id FROM applications a "
            "JOIN package_versions p ON p.application_pk=a.id AND p.fingerprint=a.current_fingerprint "
            "JOIN application_artifacts f ON f.id=p.artifact_id "
            "LEFT JOIN application_approvals ap ON ap.package_id=p.id "
            "WHERE a.application_id=? AND a.tracker=?",
            (claim.application_id, claim.tracker.value),
        ).fetchone()
        if row is None or row["status"] != "submitted":
            raise _Rejected("notification_submission_not_current")
        if row["current_fingerprint"] != claim.fingerprint:
            raise _Rejected("notification_package_stale")
        delivery = db.execute(
            "SELECT * FROM delivery_attempts WHERE application_pk=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (row["application_pk"],),
        ).fetchone()
    if (
        delivery is None
        or delivery["state"] != "submitted"
        or delivery["package_id"] != row["package_id"]
        or not delivery["receipt"]
        or not delivery["receipt"].strip()
    ):
        raise _Rejected("notification_submission_unverified")
    try:
        package = ApplicationPackage.model_validate_json(row["payload_json"])
    except ValueError:
        raise _Rejected("notification_package_invalid") from None
    if (
        package.application_id != claim.application_id
        or package.tracker != claim.tracker
        or package.fingerprint() != claim.fingerprint
        or row["approval_id"] is None
        or row["artifact_owner"] != row["application_pk"]
        or row["kind"] != "cv"
        or row["cv_hash"] != package.cv_sha256
    ):
        raise _Rejected("notification_package_invalid")
    private_root = Path(store.private_root).expanduser().resolve()
    _verified_bytes(private_root, row["cv_path"], package.cv_sha256, {".pdf"})
    proof = _verified_bytes(
        private_root,
        delivery["proof_path"],
        delivery["proof_sha256"],
        {".png", ".jpg", ".jpeg", ".webp"},
    )
    labels = (package.company, package.role, package.cv_version)
    if any(any(ord(char) < 32 or ord(char) == 127 for char in label) for label in labels):
        raise _Rejected("notification_caption_invalid")
    caption = (
        f"{'Test submission confirmation' if claim.tracker == TrackerName.TEST else 'Submission confirmed'}\n"
        f"Company: {package.company}\nRole: {package.role}\n"
        f"Exact CV: {package.cv_version}\nPDF SHA-256: {package.cv_sha256}\n"
        f"Package: {claim.fingerprint}\nTracker: {claim.tracker.value}"
    )
    if len(caption.encode("utf-16-le")) // 2 > 1024:
        raise _Rejected("notification_caption_invalid")
    return proof, Path(delivery["proof_path"]).suffix.lower(), caption


def _message_id(response: object, chat_id: int) -> int:
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise _Retry("notification_ack_invalid")
    result = response.get("result")
    if not isinstance(result, dict):
        raise _Retry("notification_ack_invalid")
    message_id, chat = result.get("message_id"), result.get("chat")
    if (
        type(message_id) is not int
        or message_id <= 0
        or not isinstance(chat, dict)
        or type(chat.get("id")) is not int
        or chat["id"] != chat_id
        or chat.get("type") != "private"
    ):
        raise _Retry("notification_ack_invalid")
    return message_id


def dispatch_submission_notifications(
    store: "ApplicationStore",
    settings: Settings,
    *,
    client_factory=TelegramClient,
    max_items: int = 5,
) -> NotificationDispatchCounts:
    """Send only verified current submission proofs; retry only this notification.

    No schema installation, delivery replay or browser cleanup occurs here.
    Configuration/transport/unknown-ack failures retry indefinitely with capped
    backoff. Invalid canonical evidence is terminally rejected for owner review.
    Client errors are never persisted verbatim, since they can contain secrets.
    """
    if type(max_items) is not int or not 0 <= max_items <= MAX_BATCH_SIZE:
        raise ValueError(f"max_items must be an integer between 0 and {MAX_BATCH_SIZE}")
    counts = {"claimed": 0, "acknowledged": 0, "retrying": 0, "rejected": 0}
    for _ in range(max_items):
        claim = _claim_next(store.database_path)
        if claim is None:
            break
        counts["claimed"] += 1
        acknowledged = False
        try:
            chat_id = _owner_chat(settings)
            proof, suffix, caption = _notification_content(store, claim)
            # send_photo reopens a path: send a private snapshot of the verified
            # bytes, not a source file that could change between hash and upload.
            with NamedTemporaryFile(dir=store.private_root, suffix=suffix) as snapshot:
                snapshot.write(proof)
                snapshot.flush()
                with client_factory(settings.telegram_bot_token) as client:
                    _renew(store.database_path, claim)
                    response = client.send_photo(chat_id, Path(snapshot.name), caption)
                    message_id = _message_id(response, chat_id)
                    acknowledged = _acknowledge(store.database_path, claim, chat_id, message_id)
                    if not acknowledged:
                        raise _Retry("notification_lease_lost")
        except _Rejected as exc:
            _fail(store.database_path, claim, str(exc), rejected=True)
            counts["rejected"] += 1
        except Exception as exc:  # noqa: BLE001 - isolate arbitrary notification client failures.
            # A client close failure after durable acknowledgement cannot undo it.
            if not acknowledged:
                code = str(exc) if isinstance(exc, _Retry) else "notification_send_failed"
                _fail(store.database_path, claim, code)
                counts["retrying"] += 1
        if acknowledged:
            counts["acknowledged"] += 1
    return NotificationDispatchCounts(**counts)


def acknowledged_submission_sessions(database_path: Path) -> list[tuple[str, TrackerName, str]]:
    """Return durable cleanup candidates; the browser owner checks its binding."""
    with _transaction(database_path) as db:
        rows = db.execute(
            "SELECT application_id,tracker,fingerprint FROM submission_notification_outbox "
            "WHERE status='acknowledged' AND message_id>0 AND chat_id>0 "
            "AND acknowledged_at IS NOT NULL ORDER BY id"
        ).fetchall()
    return [
        (row["application_id"], TrackerName(row["tracker"]), row["fingerprint"]) for row in rows
    ]
