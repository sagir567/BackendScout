"""Transactional application history in the existing workflow SQLite database.

Callers authenticate the owner before recording approval and establish external
confirmation before recording receipt. This module performs no network actions.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend_scout.application_package import ApplicationPackage
from backend_scout.config import TrackerName
from backend_scout.models import Job

SCHEMA_VERSION = 5


class ApplicationConflict(ValueError):
    """The operation conflicts with current review or delivery state."""


@dataclass(frozen=True)
class StoredPackage:
    package: ApplicationPackage
    version: int
    cv_path: str


@dataclass(frozen=True)
class DeliveryAttempt:
    attempt_id: str
    fingerprint: str
    state: str
    created: bool


@dataclass(frozen=True)
class ApplicationRecord:
    application_id: str
    tracker: TrackerName
    status: str
    current_fingerprint: str | None
    job: Job | None


def _now():
    return datetime.now(UTC).isoformat()


class ApplicationStore:
    def __init__(self, database_path: Path, private_root: Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self.private_root = Path(private_root).expanduser().resolve()
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.database_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._migrate()

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self):
        statements = (
            """CREATE TABLE IF NOT EXISTS application_schema_versions (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY, application_id TEXT NOT NULL, tracker TEXT NOT NULL
                CHECK(tracker IN ('test','production')), status TEXT NOT NULL,
                current_fingerprint TEXT, job_json TEXT, UNIQUE(application_id, tracker))""",
            """CREATE TABLE IF NOT EXISTS application_artifacts (
                id INTEGER PRIMARY KEY, application_pk INTEGER NOT NULL REFERENCES applications(id),
                path TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS package_versions (
                id INTEGER PRIMARY KEY, application_pk INTEGER NOT NULL REFERENCES applications(id),
                version INTEGER NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
                artifact_id INTEGER NOT NULL REFERENCES application_artifacts(id), created_at TEXT NOT NULL,
                UNIQUE(application_pk,version), UNIQUE(application_pk,fingerprint))""",
            """CREATE TABLE IF NOT EXISTS application_approvals (
                id INTEGER PRIMARY KEY, package_id INTEGER NOT NULL REFERENCES package_versions(id),
                actor_id TEXT NOT NULL, approved_at TEXT NOT NULL, UNIQUE(package_id))""",
            """CREATE TABLE IF NOT EXISTS delivery_attempts (
                attempt_id TEXT PRIMARY KEY, application_pk INTEGER NOT NULL REFERENCES applications(id),
                package_id INTEGER NOT NULL REFERENCES package_versions(id), request_key TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('sending','submitted','not_sent','unknown')),
                receipt TEXT, proof_path TEXT, proof_sha256 TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_application_delivery
                ON delivery_attempts(application_pk) WHERE state IN ('sending','submitted','unknown')""",
            """CREATE TABLE IF NOT EXISTS application_events (
                event_id INTEGER PRIMARY KEY, application_pk INTEGER NOT NULL REFERENCES applications(id),
                event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS projection_outbox (
                event_id INTEGER PRIMARY KEY REFERENCES application_events(event_id),
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0)""",
        )
        with self._transaction() as db:
            db.execute(statements[0])
            current = db.execute("SELECT MAX(version) FROM application_schema_versions").fetchone()[
                0
            ]
            if current is not None and current not in {1, 2, 3, 4, SCHEMA_VERSION}:
                raise ApplicationConflict("Application schema is unsupported by this release")
            if current is None:
                for statement in statements[1:]:
                    db.execute(statement)
                db.execute("INSERT INTO application_schema_versions VALUES (?,?)", (1, _now()))
                current = 1
            if current == 1:
                db.execute("""CREATE TABLE application_metadata (
                    application_pk INTEGER PRIMARY KEY REFERENCES applications(id),
                    legacy_status TEXT, claimed INTEGER NOT NULL DEFAULT 0,
                    needs_input_code TEXT, needs_input_message TEXT, revision_feedback TEXT,
                    answers_json TEXT NOT NULL DEFAULT '{}',
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                db.execute("""CREATE TABLE application_commands (
                    request_key TEXT PRIMARY KEY, application_pk INTEGER NOT NULL REFERENCES applications(id),
                    kind TEXT NOT NULL, task_id TEXT, created_at TEXT NOT NULL)""")
                now = _now()
                db.execute(
                    "INSERT INTO application_metadata(application_pk,claimed,created_at,updated_at) "
                    "SELECT id,1,?,? FROM applications WHERE current_fingerprint IS NOT NULL",
                    (now, now),
                )
                db.execute("INSERT INTO application_schema_versions VALUES (?,?)", (2, _now()))
                current = 2
            if current == 2:
                for column in ("claim_token", "lease_until", "next_attempt_at", "last_error"):
                    db.execute(f"ALTER TABLE projection_outbox ADD COLUMN {column} TEXT")
                db.execute("INSERT INTO application_schema_versions VALUES (?,?)", (3, _now()))
                current = 3
            if current == 3:
                db.execute("""CREATE TABLE IF NOT EXISTS application_form_inspections (
                    inspection_id INTEGER PRIMARY KEY,
                    application_pk INTEGER NOT NULL REFERENCES applications(id),
                    application_url TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL)""")
                db.execute("INSERT INTO application_schema_versions VALUES (?,?)", (4, _now()))
                current = 4
            if current == 4:
                db.execute("ALTER TABLE application_metadata ADD COLUMN proposed_answers_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("""CREATE TABLE application_routes (
                    route_id INTEGER PRIMARY KEY,
                    application_pk INTEGER NOT NULL REFERENCES applications(id),
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
                db.execute("""CREATE TABLE browser_observations (
                    observation_id INTEGER PRIMARY KEY,
                    application_pk INTEGER NOT NULL REFERENCES applications(id),
                    fingerprint TEXT, phase TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL)""")
                from backend_scout.browser_budget import install_budget_schema
                from backend_scout.submission_notifications import install_notification_schema

                install_budget_schema(db)
                install_notification_schema(db)
                db.execute("INSERT INTO application_schema_versions VALUES (?,?)", (5, _now()))

    def save_application_route(self, application_id, tracker, route):
        from backend_scout.application_routes import ApplicationRoute

        route = ApplicationRoute.model_validate(route)
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["job_json"]:
                job = Job.model_validate_json(app["job_json"])
                if (route.company, route.title) != (job.company, job.title):
                    raise ApplicationConflict("Route identity differs from the current job")
            db.execute(
                "INSERT INTO application_routes(application_pk,payload_json,created_at) VALUES (?,?,?)",
                (app["id"], route.model_dump_json(), _now()),
            )

    def latest_application_route(self, application_id, tracker):
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            row = db.execute(
                "SELECT payload_json FROM application_routes WHERE application_pk=? ORDER BY route_id DESC LIMIT 1",
                (app["id"],),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def record_browser_observation(self, application_id, tracker, fingerprint, phase, payload):
        if not phase or len(phase) > 80:
            raise ValueError("A bounded browser phase is required")
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 100_000:
            raise ValueError("Browser observation is too large")
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            db.execute(
                "INSERT INTO browser_observations(application_pk,fingerprint,phase,payload_json,created_at) VALUES (?,?,?,?,?)",
                (app["id"], fingerprint, phase, encoded, _now()),
            )

    def latest_browser_observation(self, application_id, tracker):
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            row = db.execute(
                "SELECT phase,fingerprint,payload_json,created_at FROM browser_observations WHERE application_pk=? ORDER BY observation_id DESC LIMIT 1",
                (app["id"],),
            ).fetchone()
            return ({"phase": row[0], "fingerprint": row[1], "details": json.loads(row[2]),
                     "created_at": row[3]} if row else None)

    def set_proposed_answers(self, application_id, tracker, answers):
        if not isinstance(answers, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or not k.strip()
            or not v.strip() or len(k) > 2000 or len(v) > 6000
            for k, v in answers.items()
        ):
            raise ValueError("Proposed answers must be bounded text")
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["status"] in {"sending", "submitted", "submission_unknown", "approved", "closed", "rejected"}:
                raise ApplicationConflict("Do not replace content after approval or submission")
            now = _now()
            db.execute(
                "INSERT INTO application_metadata(application_pk,claimed,proposed_answers_json,created_at,updated_at) VALUES (?,1,?,?,?) "
                "ON CONFLICT(application_pk) DO UPDATE SET proposed_answers_json=excluded.proposed_answers_json,updated_at=excluded.updated_at",
                (app["id"], json.dumps(answers, ensure_ascii=False), now, now),
            )

    def _artifact(self, relative_path: str, expected_hash: str | None = None):
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Artifact path must be relative to the private root")
        target = (self.private_root / path).resolve()
        if not target.is_relative_to(self.private_root) or not target.is_file():
            raise ValueError("Artifact is missing or escapes the private root")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if expected_hash is not None and digest != expected_hash:
            raise ApplicationConflict("Artifact bytes differ from the reviewed checksum")
        return path.as_posix(), digest

    @staticmethod
    def _application(db, application_id, tracker):
        row = db.execute(
            "SELECT * FROM applications WHERE application_id=? AND tracker=?",
            (application_id, TrackerName(tracker).value),
        ).fetchone()
        if row is None:
            raise ApplicationConflict("Application is not registered in this tracker")
        return row

    def register_application(self, application_id: str, tracker: TrackerName, job: Job):
        """Keep discovery data locally before a CV exists; protect reviewed facts."""
        if not isinstance(application_id, str) or not application_id.strip():
            raise ValueError("Application identity is required")
        job = Job.model_validate(job)
        with self._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO applications(application_id,tracker,status) VALUES (?,?,'discovered')",
                (application_id, TrackerName(tracker).value),
            )
            application = self._application(db, application_id, tracker)
            previous = application["job_json"]
            if previous is not None:
                old = Job.model_validate_json(previous)
                volatile = {"discovered_at", "match_score", "match_reason"}
                if old.model_dump(exclude=volatile) == job.model_dump(exclude=volatile):
                    return
                if application["current_fingerprint"] is not None:
                    raise ApplicationConflict(
                        "Job facts changed after review; reconcile before updating"
                    )
            elif application["current_fingerprint"] is not None:
                raise ApplicationConflict(
                    "Reconcile discovery data before attaching it to a reviewed package"
                )
            db.execute(
                "UPDATE applications SET job_json=? WHERE id=?",
                (job.model_dump_json(), application["id"]),
            )
            self._event(db, application["id"], "job_registered", {})

    def get_application(self, application_id: str, tracker: TrackerName) -> ApplicationRecord:
        with self._transaction() as db:
            row = self._application(db, application_id, tracker)
            return ApplicationRecord(
                row["application_id"],
                TrackerName(row["tracker"]),
                row["status"],
                row["current_fingerprint"],
                Job.model_validate_json(row["job_json"]) if row["job_json"] else None,
            )

    def import_snapshot(
        self, application_id: str, tracker: TrackerName, job: Job, legacy_status: str
    ):
        """Import job facts for review, never treating remote approval as local authority."""
        if not application_id.strip():
            raise ValueError("Application identity is required")
        job = Job.model_validate(job)
        historical = {
            "submitted",
            "closed",
            "rejected",
            "offer",
            "interview",
            "assessment",
            "recruiter_reply",
        }
        status = legacy_status if legacy_status in historical else "discovered"
        code = message = None
        if legacy_status in {
            "submission_prepared",
            "awaiting_human_verification",
            "submission_unknown",
        }:
            status = (
                "submission_unknown" if legacy_status == "submission_unknown" else "needs_input"
            )
            code = "legacy_portal_incomplete"
            message = "Previous portal preparation needs review; no new delivery is authorized."
        with self._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO applications(application_id,tracker,status,job_json) VALUES (?,?,?,?)",
                (application_id, TrackerName(tracker).value, status, job.model_dump_json()),
            )
            app = self._application(db, application_id, tracker)
            meta = db.execute(
                "SELECT * FROM application_metadata WHERE application_pk=?", (app["id"],)
            ).fetchone()
            if meta and meta["claimed"]:
                # Observation can warn about external completion, but cannot replace local review content.
                if legacy_status in {"closed", "rejected"} and app["status"] not in {
                    "sending",
                    "submitted",
                    "submission_unknown",
                }:
                    db.execute(
                        "UPDATE applications SET status=? WHERE id=?", (legacy_status, app["id"])
                    )
                elif legacy_status in historical | {"submission_unknown"} and app[
                    "status"
                ] not in historical | {"submission_unknown"}:
                    db.execute(
                        "UPDATE applications SET status='submission_unknown' WHERE id=?",
                        (app["id"],),
                    )
                    db.execute(
                        "UPDATE application_metadata SET needs_input_code='external_receipt_check', "
                        "needs_input_message='External tracker reports a later application state; reconcile before retrying.' WHERE application_pk=?",
                        (app["id"],),
                    )
                db.execute(
                    "UPDATE application_metadata SET legacy_status=? WHERE application_pk=?",
                    (legacy_status, app["id"]),
                )
                return
            if app["current_fingerprint"] is not None:
                raise ApplicationConflict("Reviewed application requires explicit reconciliation")
            db.execute(
                "UPDATE applications SET status=?,job_json=? WHERE id=?",
                (status, job.model_dump_json(), app["id"]),
            )
            now = _now()
            db.execute(
                "INSERT INTO application_metadata(application_pk,legacy_status,claimed,needs_input_code,needs_input_message,created_at,updated_at) "
                "VALUES (?,?,0,?,?,?,?) ON CONFLICT(application_pk) DO UPDATE SET "
                "legacy_status=excluded.legacy_status,needs_input_code=excluded.needs_input_code,"
                "needs_input_message=excluded.needs_input_message,updated_at=excluded.updated_at",
                (app["id"], legacy_status, code, message, now, now),
            )

    def list_records(self, tracker: TrackerName) -> list[dict]:
        with self._transaction() as db:
            rows = db.execute(
                "SELECT a.*,m.legacy_status,m.claimed,m.needs_input_code,m.needs_input_message,"
                "m.revision_feedback,m.created_at,m.updated_at FROM applications a "
                "LEFT JOIN application_metadata m ON m.application_pk=a.id WHERE a.tracker=? ORDER BY a.id DESC",
                (TrackerName(tracker).value,),
            ).fetchall()
            return [dict(row) for row in rows]

    def metadata(self, application_id: str, tracker: TrackerName) -> dict:
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            row = db.execute(
                "SELECT * FROM application_metadata WHERE application_pk=?", (app["id"],)
            ).fetchone()
            return dict(row) if row else {}

    def save_form_inspection(self, application_id: str, tracker: TrackerName,
                             application_url: str, payload: dict) -> None:
        """Record observed public form metadata without approval or lifecycle changes."""
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 100_000:
            raise ValueError("Form inspection is too large")
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            job = Job.model_validate_json(app["job_json"])
            if job.application_url != application_url:
                raise ApplicationConflict("The inspected destination changed")
            db.execute(
                "INSERT INTO application_form_inspections(application_pk,application_url,payload_json,created_at) "
                "VALUES (?,?,?,?)", (app["id"], application_url, encoded, _now()),
            )

    def latest_form_inspection(self, application_id: str, tracker: TrackerName) -> dict | None:
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            row = db.execute(
                "SELECT application_url,payload_json FROM application_form_inspections "
                "WHERE application_pk=? ORDER BY inspection_id DESC LIMIT 1", (app["id"],),
            ).fetchone()
            if row is None or row["application_url"] != Job.model_validate_json(app["job_json"]).application_url:
                return None
            return json.loads(row["payload_json"])

    def form_check_pending(self, application_id: str, tracker: TrackerName) -> bool:
        with self._transaction() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                return False
            return db.execute(
                "SELECT 1 FROM tasks WHERE kind='package_form_inspect' AND tracker=? "
                "AND status IN ('queued','running','retrying') "
                "AND json_extract(payload_json,'$.page_id')=? LIMIT 1",
                (TrackerName(tracker).value, application_id),
            ).fetchone() is not None

    def latest_decisive_form_inspection(self, application_id: str, tracker: TrackerName) -> dict | None:
        """A later timeout must not erase an observed unavailable destination."""
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            job = Job.model_validate_json(app["job_json"])
            row = db.execute(
                "SELECT payload_json FROM application_form_inspections WHERE application_pk=? "
                "AND application_url=? AND (json_extract(payload_json,'$.status')='inspected' OR "
                "(json_extract(payload_json,'$.status')='unavailable' AND "
                "json_extract(payload_json,'$.reason_code') IN "
                "('closed','identity_mismatch','unsafe_url','redirect_blocked'))) "
                "ORDER BY inspection_id DESC LIMIT 1", (app["id"], job.application_url),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def set_state(
        self,
        application_id: str,
        tracker: TrackerName,
        status: str,
        *,
        code: str | None = None,
        message: str | None = None,
        feedback: str | None = None,
    ):
        if status not in {
            "discovered",
            "drafting",
            "enriching",
            "review_ready",
            "needs_input",
            "revision_requested",
            "blocked",
            "preparing",
        }:
            raise ValueError("Unsupported service state")
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["status"] in {
                "sending",
                "submitted",
                "submission_unknown",
                "closed",
                "rejected",
                "offer",
                "interview",
                "assessment",
            }:
                raise ApplicationConflict("Resolve the current delivery or historical state first")
            now = _now()
            db.execute(
                "UPDATE applications SET status=?,current_fingerprint=CASE WHEN ?='enriching' "
                "THEN NULL ELSE current_fingerprint END WHERE id=?",
                (status, status, app["id"]),
            )
            db.execute(
                "INSERT INTO application_metadata(application_pk,claimed,needs_input_code,needs_input_message,revision_feedback,created_at,updated_at) "
                "VALUES (?,1,?,?,?,?,?) ON CONFLICT(application_pk) DO UPDATE SET claimed=1,"
                "needs_input_code=excluded.needs_input_code,needs_input_message=excluded.needs_input_message,"
                "revision_feedback=COALESCE(excluded.revision_feedback,application_metadata.revision_feedback),updated_at=excluded.updated_at",
                (app["id"], code, message, feedback, now, now),
            )
            self._event(db, app["id"], "state_changed", {"status": status, "reason_code": code})

    def revise_job(
        self, application_id: str, tracker: TrackerName, job: Job, *, allow_enriching=False
    ):
        job = Job.model_validate(job)
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["status"] == "enriching" and not allow_enriching:
                raise ApplicationConflict("Wait for the current source refresh")
            if app["status"] in {
                "drafting",
                "sending",
                "submitted",
                "submission_unknown",
                "closed",
                "rejected",
                "offer",
                "interview",
                "assessment",
            }:
                raise ApplicationConflict("Resolve the current delivery or historical state first")
            old = Job.model_validate_json(app["job_json"])
            if (old.company, old.title, old.source_url) != (job.company, job.title, job.source_url):
                raise ApplicationConflict("Job identity cannot change")
            db.execute(
                "UPDATE applications SET job_json=?,current_fingerprint=NULL,status='discovered' WHERE id=?",
                (job.model_dump_json(), app["id"]),
            )
            now = _now()
            db.execute(
                "INSERT INTO application_metadata(application_pk,claimed,created_at,updated_at) VALUES (?,1,?,?) "
                "ON CONFLICT(application_pk) DO UPDATE SET claimed=1,needs_input_code=NULL,needs_input_message=NULL,updated_at=excluded.updated_at",
                (app["id"], now, now),
            )
            self._event(db, app["id"], "job_revised", {})

    def invalidate_review(self, application_id: str, tracker: TrackerName, feedback: str):
        if not feedback.strip():
            raise ValueError("Revision feedback is required")
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["status"] in {
                "drafting",
                "enriching",
                "sending",
                "submitted",
                "submission_unknown",
                "closed",
                "rejected",
                "offer",
                "interview",
                "assessment",
                "recruiter_reply",
            }:
                raise ApplicationConflict("Resolve the delivery before revising")
            db.execute(
                "UPDATE applications SET current_fingerprint=NULL,status='revision_requested' WHERE id=?",
                (app["id"],),
            )
            now = _now()
            db.execute(
                "INSERT INTO application_metadata(application_pk,revision_feedback,claimed,created_at,updated_at) VALUES (?,?,1,?,?) "
                "ON CONFLICT(application_pk) DO UPDATE SET revision_feedback=excluded.revision_feedback,claimed=1,updated_at=excluded.updated_at",
                (app["id"], feedback, now, now),
            )
            self._event(db, app["id"], "revision_requested", {})

    def set_answers(self, application_id: str, tracker: TrackerName, answers: dict[str, str]):
        if len(answers) > 50 or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key.strip()
            or len(key) > 200
            or len(value) > 2000
            for key, value in answers.items()
        ):
            raise ValueError("Answers must be bounded text fields")
        if any(
            key.casefold().startswith(("identity.", "review."))
            or any(
                word in key.casefold()
                for word in ("password", "secret", "token", "captcha", "verification code")
            )
            for key in answers
        ):
            raise ValueError("Credentials and verification codes are entered only in the portal")
        payload = json.dumps(answers, sort_keys=True)
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["status"] in {
                "drafting",
                "enriching",
                "sending",
                "submitted",
                "submission_unknown",
                "closed",
                "rejected",
                "offer",
                "interview",
                "assessment",
            }:
                raise ApplicationConflict("Resolve delivery or history before changing answers")
            meta = db.execute(
                "SELECT answers_json FROM application_metadata WHERE application_pk=?", (app["id"],)
            ).fetchone()
            if meta and meta["answers_json"] == payload:
                return
            now = _now()
            db.execute(
                "UPDATE applications SET current_fingerprint=NULL,status='review_ready' WHERE id=?",
                (app["id"],),
            )
            db.execute(
                "INSERT INTO application_metadata(application_pk,claimed,answers_json,created_at,updated_at) "
                "VALUES (?,1,?,?,?) ON CONFLICT(application_pk) DO UPDATE SET claimed=1,answers_json=excluded.answers_json,"
                "needs_input_code=NULL,needs_input_message=NULL,questions_json='[]',updated_at=excluded.updated_at",
                (app["id"], payload, now, now),
            )
            self._event(db, app["id"], "answers_changed", {})

    def resume_approved(self, application_id: str, tracker: TrackerName, fingerprint: str):
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            current = self._current(db, app, fingerprint)
            if (
                app["status"] not in {"approved", "needs_input", "blocked"}
                or not db.execute(
                    "SELECT 1 FROM application_approvals WHERE package_id=?", (current["id"],)
                ).fetchone()
            ):
                raise ApplicationConflict("The exact current package has no reusable approval")
            if db.execute(
                "SELECT 1 FROM delivery_attempts WHERE application_pk=? AND state IN ('sending','submitted','unknown')",
                (app["id"],),
            ).fetchone():
                raise ApplicationConflict("Reconcile the previous attempt before retrying")
            db.execute("UPDATE applications SET status='approved' WHERE id=?", (app["id"],))

    def attempt_count(self, application_id: str, tracker: TrackerName) -> int:
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            return db.execute(
                "SELECT COUNT(*) FROM delivery_attempts WHERE application_pk=?", (app["id"],)
            ).fetchone()[0]

    def package_approved(self, application_id: str, tracker: TrackerName, fingerprint: str) -> bool:
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            if app["current_fingerprint"] != fingerprint:
                return False
            return bool(
                db.execute(
                    "SELECT 1 FROM package_versions p JOIN application_approvals a ON a.package_id=p.id "
                    "WHERE p.application_pk=? AND p.fingerprint=?",
                    (app["id"], fingerprint),
                ).fetchone()
            )

    def set_questions(self, application_id: str, tracker: TrackerName, questions: list):
        def valid(question):
            if isinstance(question, str):
                return len(question) <= 200
            return (isinstance(question, dict)
                    and set(question) <= {"key", "label", "required", "options", "sensitive"}
                    and isinstance(question.get("key"), str)
                    and 0 < len(question["key"]) <= 200
                    and isinstance(question.get("label"), str)
                    and 0 < len(question["label"]) <= 2000
                    and type(question.get("required", False)) is bool
                    and type(question.get("sensitive", False)) is bool
                    and isinstance(question.get("options", []), list)
                    and len(question.get("options", [])) <= 100
                    and all(isinstance(v, str) and len(v) <= 1000
                            for v in question.get("options", [])))
        if len(questions) > 50 or any(not valid(q) for q in questions):
            raise ValueError("Portal questions exceed the review limit")
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            db.execute(
                "UPDATE application_metadata SET questions_json=? WHERE application_pk=?",
                (json.dumps(questions), app["id"]),
            )

    def verified_artifact(self, relative_path: str, expected_hash: str | None = None) -> Path:
        relative, _checksum = self._artifact(relative_path, expected_hash)
        return self.private_root / relative

    def latest_delivery(self, application_id: str, tracker: TrackerName) -> dict | None:
        with self._transaction() as db:
            app = self._application(db, application_id, tracker)
            row = db.execute(
                "SELECT d.*,p.payload_json FROM delivery_attempts d JOIN package_versions p "
                "ON p.id=d.package_id WHERE d.application_pk=? ORDER BY d.created_at DESC LIMIT 1",
                (app["id"],),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _current(db, application, fingerprint=None):
        if fingerprint is not None and fingerprint != application["current_fingerprint"]:
            raise ApplicationConflict("Package is stale; review the current version")
        row = db.execute(
            "SELECT p.*, a.path AS cv_path FROM package_versions p JOIN application_artifacts a "
            "ON a.id=p.artifact_id WHERE p.application_pk=? AND p.fingerprint=?",
            (application["id"], application["current_fingerprint"]),
        ).fetchone()
        if row is None:
            raise ApplicationConflict("Application has no current review package")
        return row

    @staticmethod
    def _event(db, application_pk, kind, payload):
        event = db.execute(
            "INSERT INTO application_events(application_pk,event_type,payload_json,created_at) VALUES (?,?,?,?)",
            (application_pk, kind, json.dumps(payload, sort_keys=True), _now()),
        )
        db.execute("INSERT INTO projection_outbox(event_id) VALUES (?)", (event.lastrowid,))

    def save_package(self, package: ApplicationPackage, cv_relative_path: str) -> StoredPackage:
        package = ApplicationPackage.model_validate(package)
        relative_path, checksum = self._artifact(cv_relative_path, package.cv_sha256)
        fingerprint = package.fingerprint()
        with self._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO applications(application_id,tracker,status) VALUES (?,?,'review_ready')",
                (package.application_id, package.tracker.value),
            )
            application = self._application(db, package.application_id, package.tracker)
            now = _now()
            db.execute(
                "INSERT INTO application_metadata(application_pk,claimed,created_at,updated_at) VALUES (?,1,?,?) "
                "ON CONFLICT(application_pk) DO UPDATE SET claimed=1,updated_at=excluded.updated_at",
                (application["id"], now, now),
            )
            if application["job_json"]:
                job = Job.model_validate_json(application["job_json"])
                if (job.company, job.title) != (package.company, package.role) or (
                    job.application_url and job.application_url != package.official_destination
                ):
                    raise ApplicationConflict(
                        "Package identity or destination differs from the stored job"
                    )
            existing = db.execute(
                "SELECT * FROM package_versions WHERE application_pk=? AND fingerprint=?",
                (application["id"], fingerprint),
            ).fetchone()
            if existing is not None:
                current = self._current(db, application, fingerprint)
                self._artifact(current["cv_path"], package.cv_sha256)
                return StoredPackage(package, current["version"], current["cv_path"])
            if application["status"] in {"sending", "submitted", "submission_unknown"}:
                raise ApplicationConflict(
                    "Resolve the delivery attempt before revising its package"
                )
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM package_versions WHERE application_pk=?",
                (application["id"],),
            ).fetchone()[0]
            artifact = db.execute(
                "INSERT INTO application_artifacts(application_pk,path,sha256,kind,created_at) VALUES (?,?,?,'cv',?)",
                (application["id"], relative_path, checksum, _now()),
            )
            db.execute(
                "INSERT INTO package_versions(application_pk,version,fingerprint,payload_json,artifact_id,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    application["id"],
                    version,
                    fingerprint,
                    package.canonical_json(),
                    artifact.lastrowid,
                    _now(),
                ),
            )
            db.execute(
                "UPDATE applications SET current_fingerprint=?,status='review_ready' WHERE id=?",
                (fingerprint, application["id"]),
            )
            self._event(
                db,
                application["id"],
                "package_saved",
                {"fingerprint": fingerprint, "version": version},
            )
            return StoredPackage(package, version, relative_path)

    def get_current(self, application_id: str, tracker: TrackerName) -> StoredPackage:
        with self._transaction() as db:
            current = self._current(db, self._application(db, application_id, tracker))
            return StoredPackage(
                ApplicationPackage.model_validate_json(current["payload_json"]),
                current["version"],
                current["cv_path"],
            )

    def approve(self, application_id: str, tracker: TrackerName, fingerprint: str, actor_id: str):
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("An authenticated actor reference is required")
        with self._transaction() as db:
            application = self._application(db, application_id, tracker)
            current = self._current(db, application, fingerprint)
            package = ApplicationPackage.model_validate_json(current["payload_json"])
            self._artifact(current["cv_path"], package.cv_sha256)
            existing = db.execute(
                "SELECT id FROM application_approvals WHERE package_id=?", (current["id"],)
            ).fetchone()
            if existing:
                return
            if application["status"] != "review_ready":
                raise ApplicationConflict("Package is not available for approval")
            db.execute(
                "INSERT INTO application_approvals(package_id,actor_id,approved_at) VALUES (?,?,?)",
                (current["id"], actor_id, _now()),
            )
            db.execute("UPDATE applications SET status='approved' WHERE id=?", (application["id"],))
            self._event(db, application["id"], "package_approved", {"fingerprint": fingerprint})

    def begin_attempt(
        self, application_id: str, tracker: TrackerName, fingerprint: str, request_key: str
    ) -> DeliveryAttempt:
        if not isinstance(request_key, str) or not request_key.strip():
            raise ValueError("A stable delivery request key is required")
        with self._transaction() as db:
            application = self._application(db, application_id, tracker)
            current = self._current(db, application, fingerprint)
            existing = db.execute(
                "SELECT * FROM delivery_attempts WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                if (
                    existing["application_pk"] != application["id"]
                    or existing["package_id"] != current["id"]
                ):
                    raise ApplicationConflict("Request key belongs to another package")
                return DeliveryAttempt(
                    existing["attempt_id"], fingerprint, existing["state"], False
                )
            if application["status"] != "approved":
                raise ApplicationConflict(
                    "Current package is not approved or delivery is unresolved"
                )
            if not db.execute(
                "SELECT id FROM application_approvals WHERE package_id=?", (current["id"],)
            ).fetchone():
                raise ApplicationConflict("Approval for the exact package is missing")
            package = ApplicationPackage.model_validate_json(current["payload_json"])
            if package.delivery_route == "whatsapp":
                raise ApplicationConflict("WhatsApp requires a user-sent handoff")
            self._artifact(current["cv_path"], package.cv_sha256)
            attempt_id, now = uuid4().hex, _now()
            db.execute(
                "INSERT INTO delivery_attempts(attempt_id,application_pk,package_id,request_key,state,created_at,updated_at) "
                "VALUES (?,?,?,?,'sending',?,?)",
                (attempt_id, application["id"], current["id"], request_key, now, now),
            )
            db.execute("UPDATE applications SET status='sending' WHERE id=?", (application["id"],))
            self._event(
                db,
                application["id"],
                "delivery_started",
                {"attempt_id": attempt_id, "fingerprint": fingerprint},
            )
            return DeliveryAttempt(attempt_id, fingerprint, "sending", True)

    def finish_attempt(
        self, attempt_id: str, outcome: str, receipt: str, proof_relative_path: str | None = None
    ):
        if (
            outcome not in {"submitted", "not_sent", "unknown"}
            or not isinstance(receipt, str)
            or not receipt.strip()
        ):
            raise ValueError(
                "A supported outcome and confirmation/reconciliation evidence are required"
            )
        with self._transaction() as db:
            attempt = db.execute(
                "SELECT d.*,p.payload_json FROM delivery_attempts d JOIN package_versions p "
                "ON p.id=d.package_id WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if attempt is None:
                raise ApplicationConflict("Unknown delivery attempt")
            proof, proof_hash = (None, None)
            if proof_relative_path is not None:
                proof, proof_hash = self._artifact(proof_relative_path)
            if attempt["state"] in {"submitted", "not_sent"}:
                if (
                    attempt["state"],
                    attempt["receipt"],
                    attempt["proof_path"],
                    attempt["proof_sha256"],
                ) != (outcome, receipt, proof, proof_hash):
                    raise ApplicationConflict("Terminal delivery evidence cannot be overwritten")
                return
            package = ApplicationPackage.model_validate_json(attempt["payload_json"])
            if outcome == "submitted" and package.delivery_route == "portal" and proof is None:
                raise ApplicationConflict("Portal confirmation requires a saved screenshot")
            db.execute(
                "UPDATE delivery_attempts SET state=?,receipt=?,proof_path=?,proof_sha256=?,updated_at=? WHERE attempt_id=?",
                (outcome, receipt, proof, proof_hash, _now(), attempt_id),
            )
            status = {
                "submitted": "submitted",
                "unknown": "submission_unknown",
                "not_sent": "approved",
            }[outcome]
            db.execute(
                "UPDATE applications SET status=? WHERE id=?", (status, attempt["application_pk"])
            )
            self._event(
                db, attempt["application_pk"], "delivery_" + outcome, {"attempt_id": attempt_id}
            )
            if outcome == "submitted" and package.delivery_route == "portal":
                from backend_scout.submission_notifications import enqueue_submission_notification

                enqueue_submission_notification(
                    self.database_path, package.application_id, package.tracker,
                    package.fingerprint(), connection=db,
                )


def is_ledger_owned(database_path: Path, application_id: str, tracker: TrackerName) -> bool:
    """Read-only migration guard; old interface buttons cannot override a new package."""
    database = Path(database_path).expanduser().resolve()
    if not database.is_file():
        return False
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_metadata'"
        ).fetchone():
            return False
        return bool(
            db.execute(
                "SELECT 1 FROM application_metadata m JOIN applications a ON a.id=m.application_pk "
                "WHERE lower(replace(a.application_id,'-',''))=? AND a.tracker=? AND m.claimed=1",
                (application_id.replace("-", "").casefold(), TrackerName(tracker).value),
            ).fetchone()
        )
