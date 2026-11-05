"""At-least-once, outbound-only projection of local snapshots onto existing pages.

The store owns migrations. Version 3 adds nullable TEXT claim_token,
lease_until, next_attempt_at and last_error columns to projection_outbox.
No event payload, candidate answer, approval or employer action is replayed here.
"""

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from backend_scout.application_package import ApplicationPackage
from backend_scout.application_urls import application_entry_url
from backend_scout.config import Settings, TrackerName
from backend_scout.cv_artifacts import sha256_file
from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import NotionClient

MAX_ATTEMPTS = 5
LEASE_SECONDS = 300
RETRY_SECONDS = 60
MAX_BATCH_SIZE = 100
_REQUIRED_COLUMNS = {"claim_token", "lease_until", "next_attempt_at", "last_error"}
_STATUS_MAP = {
    "discovered": ApplicationStatus.FOUND,
    "drafting": ApplicationStatus.APPROVED_TO_TAILOR,
    "enriching": ApplicationStatus.FOUND,
    "review_ready": ApplicationStatus.CV_DRAFTED,
    "approved": ApplicationStatus.APPROVED_TO_SUBMIT,
    "needs_input": ApplicationStatus.REVISION_REQUESTED,
    "blocked": ApplicationStatus.REVISION_REQUESTED,
    "preparing": ApplicationStatus.CV_DRAFTED,
    "sending": ApplicationStatus.SUBMISSION_UNKNOWN,
}


@dataclass(frozen=True)
class ProjectionDispatchCounts:
    """Counts of application snapshots, not individual coalesced event rows."""

    claimed: int = 0
    projected: int = 0
    retrying: int = 0
    exhausted: int = 0


class ProjectionSchemaError(ValueError):
    """The parent-owned application-store migration has not been installed."""


class _ProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class _Claim:
    application_pk: int
    token: str
    count: int
    watermark: int


def _now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def _transaction(database: Path):
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
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


def _claim_next(database: Path, tracker: TrackerName) -> _Claim | None:
    now = _now()
    with _transaction(database) as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(projection_outbox)")}
        if missing := _REQUIRED_COLUMNS - columns:
            raise ProjectionSchemaError(
                "Projection outbox migration required: " + ", ".join(sorted(missing))
            )
        db.execute(
            "UPDATE projection_outbox SET status='failed',claim_token=NULL,lease_until=NULL,"
            "next_attempt_at=NULL,last_error='attempts_exhausted' WHERE attempts>=? AND "
            "(status='pending' OR (status='processing' AND (lease_until IS NULL OR lease_until<=?))) "
            "AND event_id IN (SELECT e.event_id FROM application_events e JOIN applications a "
            "ON a.id=e.application_pk WHERE a.tracker=?)",
            (MAX_ATTEMPTS, now.isoformat(), tracker.value),
        )
        candidate = db.execute(
            "SELECT e.application_pk FROM projection_outbox o JOIN application_events e "
            "ON e.event_id=o.event_id JOIN applications a ON a.id=e.application_pk "
            "WHERE a.tracker=? AND o.attempts<? AND "
            "((o.status='pending' AND (o.next_attempt_at IS NULL OR o.next_attempt_at<=?)) OR "
            "(o.status='processing' AND (o.lease_until IS NULL OR o.lease_until<=?))) "
            "AND NOT EXISTS (SELECT 1 FROM projection_outbox busy JOIN application_events be "
            "ON be.event_id=busy.event_id WHERE be.application_pk=e.application_pk "
            "AND busy.status='processing' AND busy.lease_until>?) ORDER BY o.event_id LIMIT 1",
            (tracker.value, MAX_ATTEMPTS, now.isoformat(), now.isoformat(), now.isoformat()),
        ).fetchone()
        if candidate is None:
            return None
        pk, token = candidate["application_pk"], uuid4().hex
        watermark = db.execute(
            "SELECT MAX(event_id) FROM application_events WHERE application_pk=?", (pk,)
        ).fetchone()[0]
        count = db.execute(
            "UPDATE projection_outbox SET status='processing',attempts=attempts+1,"
            "claim_token=?,lease_until=?,next_attempt_at=NULL WHERE attempts<? "
            "AND status IN ('pending','processing') AND event_id IN "
            "(SELECT event_id FROM application_events WHERE application_pk=? AND event_id<=?)",
            (
                token,
                (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                MAX_ATTEMPTS,
                pk,
                watermark,
            ),
        ).rowcount
        return _Claim(pk, token, count, watermark)


def _renew(database: Path, claim: _Claim) -> None:
    now = _now()
    with _transaction(database) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE claim_token=? "
            "AND status='processing' AND lease_until>?",
            (claim.token, now.isoformat()),
        ).fetchone()[0]
        if count != claim.count:
            raise _ProjectionError("projection_lease_lost")
        db.execute(
            "UPDATE projection_outbox SET lease_until=? WHERE claim_token=?",
            ((now + timedelta(seconds=LEASE_SECONDS)).isoformat(), claim.token),
        )


def _acknowledge(database: Path, claim: _Claim) -> bool:
    with _transaction(database) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE claim_token=? "
            "AND status='processing' AND lease_until>?",
            (claim.token, _now().isoformat()),
        ).fetchone()[0]
        if count != claim.count:
            return False
        db.execute(
            "UPDATE projection_outbox SET status='acked',claim_token=NULL,lease_until=NULL,"
            "next_attempt_at=NULL,last_error=NULL WHERE claim_token=?",
            (claim.token,),
        )
        # A successful current snapshot also supersedes older exhausted projections.
        db.execute(
            "UPDATE projection_outbox SET status='acked',last_error=NULL WHERE status='failed' "
            "AND event_id IN (SELECT event_id FROM application_events WHERE application_pk=? AND event_id<=?)",
            (claim.application_pk, claim.watermark),
        )
        return True


def _retry(database: Path, claim: _Claim, code: str) -> str:
    with _transaction(database) as db:
        attempts = db.execute(
            "SELECT MAX(attempts) FROM projection_outbox WHERE claim_token=? AND status='processing'",
            (claim.token,),
        ).fetchone()[0]
        if attempts is None:
            return "retrying"
        exhausted = attempts >= MAX_ATTEMPTS
        available = (
            None
            if exhausted
            else (
                _now() + timedelta(seconds=min(3600, RETRY_SECONDS * 2 ** (attempts - 1)))
            ).isoformat()
        )
        db.execute(
            "UPDATE projection_outbox SET status=?,next_attempt_at=?,last_error=?,"
            "claim_token=NULL,lease_until=NULL WHERE claim_token=? AND status='processing'",
            ("failed" if exhausted else "pending", available, code, claim.token),
        )
        return "exhausted" if exhausted else "retrying"


def _target(settings: Settings, tracker: TrackerName) -> str:
    target = (
        settings.notion_test_applications_data_source_id
        if tracker == TrackerName.TEST
        else settings.notion_production_applications_data_source_id
    )
    if not settings.notion_api_key or not target or not target.strip():
        raise _ProjectionError("projection_configuration_missing")
    other = (
        settings.notion_production_applications_data_source_id
        if tracker == TrackerName.TEST
        else settings.notion_test_applications_data_source_id
    )
    if other and _identity(target) == _identity(other):
        raise _ProjectionError("projection_trackers_overlap")
    return target.strip()


def _identity(value: str) -> str:
    return value.strip().replace("-", "").casefold()


def _public_url(value: str) -> bool:
    parsed = urlsplit(value)
    return application_entry_url(value) is not None and not (
        {key.casefold() for key in parse_qs(parsed.query, keep_blank_values=True)}
        & {
            "api_key",
            "key",
            "secret",
            "client_secret",
            "authorization",
            "session",
            "session_token",
            "password",
            "access_token",
            "id_token",
            "token",
            "code",
        }
    )


def _safe_job(job: Job, settings: Settings) -> Job:
    secrets = tuple(
        value
        for value in (settings.notion_api_key, settings.openai_api_key, settings.telegram_bot_token)
        if value
    )
    for url in (job.source_url, job.application_url):
        if url and (any(secret in url for secret in secrets) or not _public_url(url)):
            raise _ProjectionError("projection_unsafe_job_url")

    def clean(text: str) -> str:
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        text = re.sub(
            r"(?i)\b(?:(?:bearer|basic)\s+\S+|(?:password|access_token|id_token|api_key|client_secret|"
            r"session_token|authorization|secret|token)\s*[:=]\s*\S+)",
            "[redacted]",
            text,
        )
        return re.sub(
            r"https?://[^\s<>]+",
            lambda match: match[0] if _public_url(match[0]) else "[redacted URL]",
            text,
        )

    values = job.model_dump()
    for key, value in values.items():
        if isinstance(value, str):
            values[key] = clean(value)
        elif key == "required_skills":
            values[key] = [clean(item)[:100] for item in value[:100]]
    return Job.model_validate(values)


def _submission_record(database: Path, claim: _Claim, application: sqlite3.Row, settings: Settings):
    with _transaction(database) as db:
        attempt = db.execute(
            "SELECT d.proof_path,d.proof_sha256,d.updated_at,p.payload_json,a.path AS cv_path "
            "FROM delivery_attempts d JOIN package_versions p ON p.id=d.package_id "
            "JOIN application_artifacts a ON a.id=p.artifact_id WHERE d.application_pk=? "
            "AND d.state='submitted' AND p.fingerprint=? ORDER BY d.updated_at DESC LIMIT 1",
            (claim.application_pk, application["current_fingerprint"]),
        ).fetchone()
    if attempt is None or not attempt["proof_path"] or not attempt["proof_sha256"]:
        raise _ProjectionError("projection_submission_proof_missing")
    package = ApplicationPackage.model_validate_json(attempt["payload_json"])
    if (
        package.application_id != application["application_id"]
        or package.tracker.value != application["tracker"]
        or package.fingerprint() != application["current_fingerprint"]
    ):
        raise _ProjectionError("projection_submission_identity_mismatch")
    private_root = settings.cv_archive_root.expanduser().resolve().parent
    for value, checksum in (
        (attempt["proof_path"], attempt["proof_sha256"]),
        (attempt["cv_path"], package.cv_sha256),
    ):
        relative = Path(value)
        path = (private_root / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.is_relative_to(private_root)
        ):
            raise _ProjectionError("projection_submission_proof_invalid")
        if not path.is_file() or sha256_file(path) != checksum:
            raise _ProjectionError("projection_submission_proof_invalid")
    proof_path = attempt["proof_path"]
    if (
        Path(proof_path).suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp"}
        or any(ord(char) < 32 for char in proof_path)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", package.cv_version)
        or len(proof_path) > 1000
    ):
        raise _ProjectionError("projection_submission_proof_invalid")
    record = (
        "Verified local submission.\n"
        f"Timestamp: {attempt['updated_at']}\nPortal: {package.official_destination}\n"
        f"CV version: {package.cv_version}\nCV SHA-256: {package.cv_sha256}\n"
        f"Screenshot: {proof_path}\nScreenshot SHA-256: {attempt['proof_sha256']}"
    )
    if any(
        value and value in record
        for value in (settings.notion_api_key, settings.openai_api_key, settings.telegram_bot_token)
    ):
        raise _ProjectionError("projection_submission_proof_invalid")
    return package.delivery_route, record


def _project(database: Path, claim: _Claim, settings: Settings, tracker: TrackerName) -> None:
    target = _target(settings, tracker)
    with _transaction(database) as db:
        application = db.execute(
            "SELECT * FROM applications WHERE id=?", (claim.application_pk,)
        ).fetchone()
    if application is None or application["tracker"] != tracker.value:
        raise _ProjectionError("projection_application_missing")
    page_id = application["application_id"]
    if not re.fullmatch(r"[A-Za-z0-9-]+", page_id):
        raise _ProjectionError("projection_page_identity_invalid")
    job = _safe_job(Job.model_validate_json(application["job_json"]), settings)
    state = application["status"]
    status = _STATUS_MAP.get(state)
    if status is None:
        try:
            status = ApplicationStatus(state)
        except ValueError:
            status = ApplicationStatus.REVISION_REQUESTED
    submission = (
        _submission_record(database, claim, application, settings) if state == "submitted" else None
    )
    _renew(database, claim)
    with NotionClient(settings.notion_api_key, settings.notion_api_version) as client:
        page = client.retrieve_page(page_id)
        parent = page.get("parent", {})
        if (
            not isinstance(parent, dict)
            or parent.get("type") != "data_source_id"
            or not isinstance(parent.get("data_source_id"), str)
            or _identity(parent["data_source_id"]) != _identity(target)
            or _identity(page.get("id", "")) != _identity(page_id)
            or page.get("archived")
            or page.get("in_trash")
        ):
            raise _ProjectionError("projection_page_parent_mismatch")
        _renew(database, claim)
        client.update_job_page(page_id, job)
        _renew(database, claim)
        if submission:
            client.record_submission(
                page_id, submission[0], "Verified local application ledger", submission[1]
            )
        else:
            client.update_application_status(page_id, status)


def dispatch_projection(
    settings: Settings, tracker: TrackerName, limit: int = 20
) -> ProjectionDispatchCounts:
    """Project at most limit current applications; failures never authorize delivery.

    Each application has one leased claim, even when later events arrive. Retries
    overwrite the same properties/record, never append, create pages or send mail.
    Exhausted rows remain durable and unacknowledged; a fresh event can supersede
    them after its latest snapshot projects successfully.
    """
    tracker = TrackerName(tracker)
    if type(limit) is not int or not 0 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"Projection limit must be an integer between 0 and {MAX_BATCH_SIZE}")
    database = settings.workflow_database_path.expanduser()
    counts = {"claimed": 0, "projected": 0, "retrying": 0, "exhausted": 0}
    if not database.is_file():
        return ProjectionDispatchCounts()
    for _ in range(limit):
        claim = _claim_next(database, tracker)
        if claim is None:
            break
        counts["claimed"] += 1
        try:
            _project(database, claim, settings, tracker)
        except Exception as exc:  # noqa: BLE001 - connector failures remain private, durable retries.
            code = str(exc) if isinstance(exc, _ProjectionError) else "notion_projection_failed"
            counts[_retry(database, claim, code)] += 1
        else:
            counts["projected" if _acknowledge(database, claim) else "retrying"] += 1
    return ProjectionDispatchCounts(**counts)
