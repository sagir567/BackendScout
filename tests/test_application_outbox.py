import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest

from backend_scout import application_outbox as outbox
from backend_scout.application_package import ApplicationPackage, EmailContent
from backend_scout.application_store import ApplicationStore
from backend_scout.config import Settings, TrackerName
from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import NotionClient


@pytest.fixture
def projection(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    (private / "cv.pdf").write_bytes(b"synthetic reviewed CV")
    (private / "receipt.png").write_bytes(b"synthetic screenshot")
    store = ApplicationStore(tmp_path / "workflow.sqlite3", private)
    with sqlite3.connect(store.database_path) as db:
        existing = {row[1] for row in db.execute("PRAGMA table_info(projection_outbox)")}
        assert outbox._REQUIRED_COLUMNS <= existing
    settings = Settings.model_construct(
        workflow_database_path=store.database_path,
        cv_archive_root=private / "cv-archive",
        notion_api_key="synthetic-notion-key",
        notion_test_applications_data_source_id="test-source",
        notion_production_applications_data_source_id="production-source",
        openai_api_key="synthetic-openai-key",
        telegram_bot_token="synthetic-telegram-token",
    )
    job = Job(
        source="fixture",
        source_url="https://jobs.example.test/123",
        application_url="https://jobs.example.test/123/apply",
        company="Synthetic Employer",
        title="Backend Engineer",
        description="Build Python APIs and maintain backend services.",
        required_skills=["Python"],
        match_score=85,
    )
    client = create_autospec(NotionClient, instance=True)
    client.__enter__.return_value = client
    client.retrieve_page.side_effect = lambda page_id: {
        "id": page_id,
        "parent": {"type": "data_source_id", "data_source_id": "test-source"},
        "properties": {"Status": {"status": {"name": "approved_to_submit"}}},
    }
    connector = Mock(return_value=client)
    monkeypatch.setattr(outbox, "NotionClient", connector)
    clock = SimpleNamespace(now=datetime(2026, 11, 1, 12, tzinfo=UTC))
    monkeypatch.setattr(outbox, "_now", lambda: clock.now)
    return SimpleNamespace(
        store=store,
        settings=settings,
        job=job,
        client=client,
        connector=connector,
        clock=clock,
    )


def register(projection, page_id="page-123", tracker=TrackerName.TEST, status="discovered"):
    projection.store.register_application(page_id, tracker, projection.job)
    with sqlite3.connect(projection.store.database_path) as db:
        db.execute(
            "UPDATE applications SET status=? WHERE application_id=? AND tracker=?",
            (status, page_id, tracker.value),
        )


def rows(projection):
    with sqlite3.connect(projection.store.database_path) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row) for row in db.execute("SELECT * FROM projection_outbox ORDER BY event_id")
        ]


def dispatch(projection, limit=20, tracker=TrackerName.TEST):
    return outbox.dispatch_projection(projection.settings, tracker, limit)


def test_dispatch_updates_only_existing_page_and_acknowledges(projection):
    register(projection)

    result = dispatch(projection)

    assert result == outbox.ProjectionDispatchCounts(claimed=1, projected=1)
    projection.client.update_job_page.assert_called_once_with("page-123", projection.job)
    projection.client.update_application_status.assert_called_once_with(
        "page-123", ApplicationStatus.FOUND
    )
    projection.client.create_job_page.assert_not_called()
    projection.client.find_job_page.assert_not_called()
    projection.client.query_data_source.assert_not_called()
    projection.client.append_submission_record.assert_not_called()
    assert rows(projection)[0]["status"] == "acked"
    assert rows(projection)[0]["claim_token"] is None
    assert dispatch(projection) == outbox.ProjectionDispatchCounts()
    assert projection.store.get_application("page-123", TrackerName.TEST).status == "discovered"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("discovered", ApplicationStatus.FOUND),
        ("drafting", ApplicationStatus.APPROVED_TO_TAILOR),
        ("review_ready", ApplicationStatus.CV_DRAFTED),
        ("approved", ApplicationStatus.APPROVED_TO_SUBMIT),
        ("needs_input", ApplicationStatus.REVISION_REQUESTED),
        ("blocked", ApplicationStatus.REVISION_REQUESTED),
        ("preparing", ApplicationStatus.CV_DRAFTED),
        ("sending", ApplicationStatus.SUBMISSION_UNKNOWN),
        ("submission_unknown", ApplicationStatus.SUBMISSION_UNKNOWN),
        ("revision_requested", ApplicationStatus.REVISION_REQUESTED),
        ("closed", ApplicationStatus.CLOSED),
        ("new_unsupported_state", ApplicationStatus.REVISION_REQUESTED),
    ],
)
def test_conservative_outbound_status_mapping(projection, state, expected):
    register(projection, status=state)

    assert dispatch(projection).projected == 1

    projection.client.update_application_status.assert_called_once_with("page-123", expected)
    assert projection.store.get_application("page-123", TrackerName.TEST).status == state
    with sqlite3.connect(projection.store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM application_approvals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0] == 0


def test_events_coalesce_latest_snapshot_not_event_payload(projection):
    register(projection)
    projection.store.set_state("page-123", TrackerName.TEST, "drafting")
    projection.store.set_state("page-123", TrackerName.TEST, "review_ready")
    with sqlite3.connect(projection.store.database_path) as db:
        db.execute(
            "UPDATE application_events SET payload_json=?",
            (json.dumps({"status": "submitted", "answers": "private-answer"}),),
        )

    assert dispatch(projection) == outbox.ProjectionDispatchCounts(claimed=1, projected=1)

    projection.client.update_application_status.assert_called_once_with(
        "page-123", ApplicationStatus.CV_DRAFTED
    )
    assert len(rows(projection)) == 3
    assert {row["status"] for row in rows(projection)} == {"acked"}
    assert "private-answer" not in str(projection.client.mock_calls)


@pytest.mark.parametrize(
    "page",
    [
        {"id": "page-123"},
        {"id": "page-123", "parent": None},
        {"id": "page-123", "parent": {"type": "database_id", "database_id": "test-source"}},
        {
            "id": "page-123",
            "parent": {"type": "data_source_id", "data_source_id": "production-source"},
        },
        {"id": "other-page", "parent": {"type": "data_source_id", "data_source_id": "test-source"}},
        {
            "id": "page-123",
            "archived": True,
            "parent": {"type": "data_source_id", "data_source_id": "test-source"},
        },
    ],
)
def test_wrong_or_unverifiable_parent_is_retryable_without_mutation(projection, page):
    register(projection)
    projection.client.retrieve_page.side_effect = None
    projection.client.retrieve_page.return_value = page

    assert dispatch(projection).retrying == 1

    projection.client.update_job_page.assert_not_called()
    projection.client.update_application_status.assert_not_called()
    assert rows(projection)[0]["last_error"] == "projection_page_parent_mismatch"
    assert rows(projection)[0]["status"] == "pending"


@pytest.mark.parametrize("misconfiguration", ["legacy_only", "no_key", "same_source"])
def test_explicit_tracker_configuration_is_required(projection, misconfiguration):
    register(projection)
    if misconfiguration == "legacy_only":
        projection.settings.notion_test_applications_data_source_id = None
        projection.settings.notion_applications_data_source_id = "test-source"
    elif misconfiguration == "no_key":
        projection.settings.notion_api_key = None
    else:
        projection.settings.notion_production_applications_data_source_id = "test-source"

    assert dispatch(projection).retrying == 1

    projection.connector.assert_not_called()
    assert rows(projection)[0]["status"] == "pending"


def test_production_dispatch_does_not_claim_test_events(projection):
    register(projection)
    register(projection, "production-page", TrackerName.PRODUCTION)
    projection.client.retrieve_page.side_effect = lambda page_id: {
        "id": page_id,
        "parent": {"type": "data_source_id", "data_source_id": "production-source"},
    }

    assert dispatch(projection, tracker=TrackerName.PRODUCTION).projected == 1

    assert [row["status"] for row in rows(projection)] == ["pending", "acked"]
    projection.client.update_job_page.assert_called_once_with("production-page", projection.job)


def test_failure_has_durable_backoff_and_bounded_attempts_without_secret_error(projection):
    register(projection)
    projection.client.retrieve_page.side_effect = RuntimeError(
        "synthetic-notion-key private-answer"
    )
    for attempt in range(1, outbox.MAX_ATTEMPTS + 1):
        result = dispatch(projection)
        row = rows(projection)[0]
        assert row["attempts"] == attempt
        assert row["last_error"] == "notion_projection_failed"
        if attempt < outbox.MAX_ATTEMPTS:
            assert result.retrying == 1
            due = datetime.fromisoformat(row["next_attempt_at"])
            assert (due - projection.clock.now).total_seconds() == outbox.RETRY_SECONDS * 2 ** (
                attempt - 1
            )
            assert dispatch(projection).claimed == 0
            projection.clock.now = due
        else:
            assert result.exhausted == 1
            assert row["status"] == "failed"
    projection.clock.now += timedelta(days=10)
    assert dispatch(projection).claimed == 0
    assert projection.client.retrieve_page.call_count == outbox.MAX_ATTEMPTS


def test_fresh_event_supersedes_exhausted_projection(projection):
    register(projection)
    with sqlite3.connect(projection.store.database_path) as db:
        db.execute(
            "UPDATE projection_outbox SET status='failed',attempts=?", (outbox.MAX_ATTEMPTS,)
        )
    projection.store.set_state("page-123", TrackerName.TEST, "review_ready")

    assert dispatch(projection).projected == 1

    assert {row["status"] for row in rows(projection)} == {"acked"}
    assert [row["attempts"] for row in rows(projection)] == [outbox.MAX_ATTEMPTS, 1]


def test_claims_are_transactional_and_lease_recovery_fences_old_ack(projection):
    register(projection)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda _: outbox._claim_next(projection.store.database_path, TrackerName.TEST),
                range(2),
            )
        )
    assert sum(claim is not None for claim in claims) == 1
    first = next(claim for claim in claims if claim is not None)
    projection.clock.now += timedelta(seconds=outbox.LEASE_SECONDS + 1)
    recovered = outbox._claim_next(projection.store.database_path, TrackerName.TEST)

    assert recovered.token != first.token
    assert not outbox._acknowledge(projection.store.database_path, first)
    assert rows(projection)[0]["claim_token"] == recovered.token
    assert outbox._acknowledge(projection.store.database_path, recovered)
    assert rows(projection)[0]["attempts"] == 2


def test_active_application_lease_blocks_even_new_events(projection):
    register(projection)
    claim = outbox._claim_next(projection.store.database_path, TrackerName.TEST)
    projection.store.set_state("page-123", TrackerName.TEST, "review_ready")
    register(projection, "another-page")

    assert dispatch(projection).projected == 1

    projection.client.update_job_page.assert_called_once_with("another-page", projection.job)
    assert rows(projection)[0]["claim_token"] == claim.token
    assert rows(projection)[1]["status"] == "pending"


def test_new_event_during_projection_is_not_acknowledged_by_old_claim(projection):
    register(projection)
    projection.client.update_job_page.side_effect = lambda *_: projection.store.set_state(
        "page-123", TrackerName.TEST, "review_ready"
    )

    assert dispatch(projection, limit=1).projected == 1

    assert [row["status"] for row in rows(projection)] == ["acked", "pending"]
    projection.client.update_job_page.side_effect = None
    assert dispatch(projection).projected == 1
    assert (
        projection.client.update_application_status.call_args.args[-1]
        == ApplicationStatus.CV_DRAFTED
    )


def submit(projection):
    register(projection)
    package = ApplicationPackage(
        application_id="page-123",
        tracker=TrackerName.TEST,
        company=projection.job.company,
        role=projection.job.title,
        official_destination=projection.job.application_url,
        delivery_route="portal",
        cv_version="v3",
        cv_sha256=hashlib.sha256(b"synthetic reviewed CV").hexdigest(),
        answers=({"key": "personal question", "value": "private-answer"},),
    )
    projection.store.save_package(package, "cv.pdf")
    projection.store.approve("page-123", TrackerName.TEST, package.fingerprint(), "fixture-owner")
    attempt = projection.store.begin_attempt(
        "page-123", TrackerName.TEST, package.fingerprint(), "fixture-attempt"
    )
    projection.store.finish_attempt(
        attempt.attempt_id, "submitted", "private-answer synthetic-notion-key", "receipt.png"
    )
    return package


def test_submission_projects_only_verified_proof_and_cv_metadata(projection):
    package = submit(projection)

    assert dispatch(projection).projected == 1

    args = projection.client.record_submission.call_args.args
    assert args[:3] == ("page-123", "portal", "Verified local application ledger")
    assert "CV version: v3" in args[3]
    assert package.cv_sha256 in args[3]
    assert "Screenshot: receipt.png" in args[3]
    assert hashlib.sha256(b"synthetic screenshot").hexdigest() in args[3]
    assert "private-answer" not in str(projection.client.mock_calls)
    assert "synthetic-notion-key" not in str(projection.client.mock_calls)
    projection.client.update_application_status.assert_not_called()
    projection.client.append_submission_record.assert_not_called()
    assert dispatch(projection).claimed == 0
    with sqlite3.connect(projection.store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM application_approvals").fetchone()[0] == 1


@pytest.mark.parametrize(
    "damage", ["changed_screenshot", "missing_screenshot", "changed_cv", "outside_path"]
)
def test_invalid_submission_proof_is_never_published(projection, damage, tmp_path):
    submit(projection)
    if damage == "changed_screenshot":
        (projection.store.private_root / "receipt.png").write_bytes(b"changed")
    elif damage == "missing_screenshot":
        (projection.store.private_root / "receipt.png").unlink()
    elif damage == "changed_cv":
        (projection.store.private_root / "cv.pdf").write_bytes(b"changed")
    else:
        with sqlite3.connect(projection.store.database_path) as db:
            db.execute(
                "UPDATE delivery_attempts SET proof_path=?", (str(tmp_path / "outside.png"),)
            )

    assert dispatch(projection).retrying == 1

    projection.connector.assert_not_called()
    assert {row["status"] for row in rows(projection)} == {"pending"}
    assert projection.store.get_application("page-123", TrackerName.TEST).status == "submitted"


def test_historical_submission_without_local_proof_stays_retryable(projection):
    register(projection, status="submitted")

    assert dispatch(projection).retrying == 1

    projection.client.record_submission.assert_not_called()
    assert rows(projection)[0]["last_error"] == "projection_submission_proof_missing"


def submit_email(projection, damage=None):
    register(projection)
    package = ApplicationPackage(
        application_id="page-123", tracker=TrackerName.TEST,
        company=projection.job.company, role=projection.job.title,
        official_destination=projection.job.application_url, delivery_route="email",
        cv_version="v3", cv_sha256=hashlib.sha256(b"synthetic reviewed CV").hexdigest(),
        email=EmailContent(recipient="careers@example.test", subject="Application", body="Reviewed note"),
    )
    projection.store.save_package(package, "cv.pdf")
    projection.store.approve("page-123", TrackerName.TEST, package.fingerprint(), "fixture-owner")
    attempt = projection.store.begin_attempt("page-123", TrackerName.TEST, package.fingerprint(), "email-1")
    receipt = {
        "channel": "email", "recipient": package.email.recipient,
        "contact_source": package.official_destination, "gmail_message_id": "abc123def456",
        "cv_version": package.cv_version, "cv_sha256": package.cv_sha256,
    }
    if damage:
        receipt[damage] = "wrong-value"
    projection.store.finish_attempt(attempt.attempt_id, "submitted", json.dumps(receipt))
    return package


def test_email_submission_projects_gmail_receipt_without_screenshot(projection):
    package = submit_email(projection)
    assert dispatch(projection).projected == 1
    args = projection.client.record_submission.call_args.args
    assert args[:3] == ("page-123", "email", "Verified local application ledger")
    assert "Gmail message ID: abc123def456" in args[3]
    assert package.cv_sha256 in args[3]
    assert "Screenshot" not in args[3]
    assert "Reviewed note" not in args[3]
    assert dispatch(projection).claimed == 0


@pytest.mark.parametrize("damage", ["recipient", "contact_source", "cv_version", "cv_sha256", "gmail_message_id", "channel"])
def test_email_submission_rejects_mismatched_receipt(projection, damage):
    submit_email(projection, damage)
    assert dispatch(projection).retrying == 1
    projection.client.record_submission.assert_not_called()


def test_email_submission_rechecks_attachment_checksum(projection):
    submit_email(projection)
    (projection.store.private_root / "cv.pdf").write_bytes(b"changed")
    assert dispatch(projection).retrying == 1
    projection.client.record_submission.assert_not_called()


def test_partial_remote_update_retries_assignment_not_append_or_delivery(projection):
    submit(projection)
    projection.client.record_submission.side_effect = RuntimeError("temporary failure")
    assert dispatch(projection).retrying == 1
    first_record = projection.client.record_submission.call_args
    projection.clock.now += timedelta(seconds=outbox.RETRY_SECONDS)
    projection.client.record_submission.side_effect = None

    assert dispatch(projection).projected == 1

    assert projection.client.record_submission.call_count == 2
    assert projection.client.record_submission.call_args == first_record
    projection.client.append_submission_record.assert_not_called()
    with sqlite3.connect(projection.store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0] == 1


def test_job_projection_redacts_credentials_without_changing_local_job(projection):
    projection.job.description += (
        " synthetic-openai-key token=unknown-value https://example.test/?access_token=private"
    )
    register(projection)

    assert dispatch(projection).projected == 1

    remote_job = projection.client.update_job_page.call_args.args[1]
    assert "synthetic-openai-key" not in remote_job.description
    assert "unknown-value" not in remote_job.description
    assert "access_token=private" not in remote_job.description
    assert (
        "synthetic-openai-key"
        in projection.store.get_application("page-123", TrackerName.TEST).job.description
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.test/job",
        "https://example.test/job?token=private",
        "https://example.test/job?api_key=private",
        "https://example.test/synthetic-notion-key",
        "javascript:alert(1)",
    ],
)
def test_credential_or_unsafe_urls_never_leave_the_service(projection, url):
    projection.job.application_url = url
    register(projection)

    assert dispatch(projection).retrying == 1

    projection.connector.assert_not_called()
    assert rows(projection)[0]["last_error"] == "projection_unsafe_job_url"


def test_dispatch_limit_counts_applications_not_coalesced_events(projection):
    for number in range(3):
        register(projection, f"page-{number}")
        projection.store.set_state(f"page-{number}", TrackerName.TEST, "drafting")

    assert dispatch(projection, limit=2) == outbox.ProjectionDispatchCounts(claimed=2, projected=2)
    assert [row["status"] for row in rows(projection)].count("pending") == 2


@pytest.mark.parametrize("limit", [-1, 101, True, 1.5, "20"])
def test_invalid_limit_never_claims(projection, limit):
    register(projection)
    with pytest.raises(ValueError, match="Projection limit"):
        dispatch(projection, limit=limit)
    assert rows(projection)[0]["attempts"] == 0
    projection.connector.assert_not_called()


def test_zero_limit_and_missing_database_are_noops(projection, tmp_path):
    register(projection)
    assert dispatch(projection, limit=0) == outbox.ProjectionDispatchCounts()
    projection.settings.workflow_database_path = tmp_path / "not-created.sqlite3"
    assert dispatch(projection) == outbox.ProjectionDispatchCounts()
    assert not projection.settings.workflow_database_path.exists()
    projection.connector.assert_not_called()


def test_missing_parent_migration_is_reported_without_altering_schema(projection, tmp_path):
    database = tmp_path / "v2-only.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE projection_outbox(event_id INTEGER PRIMARY KEY,status TEXT,attempts INTEGER)"
        )
    projection.settings.workflow_database_path = database

    with pytest.raises(outbox.ProjectionSchemaError, match="claim_token"):
        dispatch(projection)

    with sqlite3.connect(database) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(projection_outbox)")] == [
            "event_id",
            "status",
            "attempts",
        ]
    projection.connector.assert_not_called()
