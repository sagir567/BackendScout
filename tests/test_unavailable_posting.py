import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from backend_scout.config import TrackerName
from backend_scout.delivery_readiness import posting_observation_blocker
from backend_scout.greenhouse_forms import GreenhouseFormInspection
from backend_scout.task_queue import QueuedTaskStatus, list_tasks, mark_task_status

T = TrackerName.TEST


def observation(code="closed", status="unavailable"):
    return GreenhouseFormInspection(status, code, "Public source observation", None,
                                    datetime.now(UTC).isoformat())


def record_observation(service, code="closed", status="unavailable"):
    job = service.store.get_application("example-job", T).job
    service.store.save_form_inspection("example-job", T, job.application_url,
                                       asdict(observation(code, status)))


def test_dead_route_is_excluded_but_cv_and_package_are_preserved(review_service):
    service = review_service
    original = service.cv_artifact("example-job", T).read_bytes()
    record_observation(service)
    detail = service.detail("example-job", T)
    assert detail["package"] is not None
    assert detail["package_blocker"]["reason_code"] == "posting_unavailable"
    assert detail["can_resume"] is False
    assert detail["item"]["delivery_readiness"]["state"] == "unavailable"
    assert service.overview(T)["counts"]["agent_apply"] == 0
    assert service.cv_artifact("example-job", T).read_bytes() == original
    assert service.store.get_application("example-job", T).status != "closed"


def test_unavailable_posting_cannot_approve_or_enqueue_or_redraft(review_service):
    service = review_service
    package, _ = service.preview_package("example-job", T)
    record_observation(service)
    result = service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    assert result.status == "blocked" and result.reason_code == "posting_unavailable"
    assert service.request_tailoring("example-job", T, "12345").status == "blocked"
    assert service.request_revision("example-job", T, "12345", "Shorten").status == "blocked"
    assert not list_tasks(service.store.database_path)
    assert not service.store.package_approved("example-job", T, package.fingerprint())


def test_closed_observation_blocks_existing_approval_resume_and_queued_worker(review_service):
    service = review_service
    package, _ = service.preview_package("example-job", T)
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    record_observation(service)
    service.delivery_adapter = Mock(side_effect=AssertionError("Must not open or submit"))
    assert service.queue_delivery("example-job", T, "12345", package.fingerprint()).status == "blocked"
    assert service.deliver("example-job", T, package.fingerprint(), "old-queued").status == "blocked"
    service.delivery_adapter.assert_not_called()
    assert service.store.attempt_count("example-job", T) == 0
    assert len(list_tasks(service.store.database_path)) == 1
    assert service.store.package_approved("example-job", T, package.fingerprint())
    record_observation(service, "public_schema_inspected", "inspected")
    assert service.detail("example-job", T)["package_blocker"] is None
    assert service.detail("example-job", T)["can_resume"] is True


def test_worker_checks_greenhouse_availability_before_model_call(review_service):
    service = review_service
    service.route_checker = Mock(return_value=observation())
    service.drafter = Mock(side_effect=AssertionError("No paid draft for unavailable route"))
    service.request_tailoring("example-job", T, "12345")
    result = service.draft("example-job", T)
    assert result.reason_code == "posting_unavailable"
    assert service.store.get_application("example-job", T).status == "needs_input"
    service.drafter.assert_not_called()
    assert service.cv_artifact("example-job", T).is_file()


def test_successful_recheck_clears_only_route_failure_without_sending(review_service):
    service = review_service
    service.route_checker = lambda _: observation()
    service.request_tailoring("example-job", T, "12345")
    service.draft("example-job", T)
    service.route_checker = lambda _: observation("public_schema_inspected", "inspected")
    service._refresh_route_observation("example-job", T)
    detail = service.detail("example-job", T)
    assert detail["package_blocker"] is None
    assert detail["item"]["needs_input_code"] is None
    assert detail["item"]["status"] == "review_ready"
    assert detail["package"]["approved"] is False
    assert service.store.attempt_count("example-job", T) == 0


def test_explicit_cv_retry_does_not_reuse_a_terminal_task(review_service):
    service = review_service
    first = service.request_tailoring("example-job", T, "12345")
    mark_task_status(first.task_id, QueuedTaskStatus.BLOCKED, path=service.store.database_path)
    service.store.set_state("example-job", T, "needs_input", code="cv_draft_needs_review")
    retry = service.request_tailoring("example-job", T, "12345")
    assert retry.task_id != first.task_id
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    tasks = list_tasks(service.store.database_path)
    assert len(tasks) == 2 and tasks[-1].status == QueuedTaskStatus.QUEUED


def test_delivery_worker_rechecks_before_creating_attempt_or_uploading(review_service):
    service = review_service
    package, _ = service.preview_package("example-job", T)
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    service.route_checker = Mock(return_value=observation())
    service.delivery_adapter = Mock(side_effect=AssertionError("No upload or submit"))
    result = service.deliver("example-job", T, package.fingerprint(), "authorized-queued")
    assert result.reason_code == "posting_unavailable"
    service.delivery_adapter.assert_not_called()
    assert service.store.attempt_count("example-job", T) == 0


@pytest.mark.parametrize("code", ["fetch_timeout", "fetch_failed", "source_gated", "unknown_schema"])
def test_incomplete_api_check_does_not_falsely_close_or_block_portal(review_service, code):
    record_observation(review_service, code)
    detail = review_service.detail("example-job", T)
    assert detail["package_blocker"] is None
    assert detail["item"]["delivery_readiness"]["state"] == "unverified"
    assert review_service.store.get_application("example-job", T).status != "closed"


def test_changed_official_url_clears_old_observation_and_requires_new_approval(review_service):
    service = review_service
    package, _ = service.preview_package("example-job", T)
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    record_observation(service)
    job = service.store.get_application("example-job", T).job
    service.update_details("example-job", T, "12345", description=job.description,
                           official_url=job.application_url.replace("fixture", "replacement"),
                           years_experience=job.years_experience)
    detail = service.detail("example-job", T)
    assert detail["package_blocker"] is None
    assert detail["package"]["fingerprint"] != package.fingerprint()
    assert detail["package"]["approved"] is False
    assert service.cv_artifact("example-job", T).is_file()


def test_malformed_observation_reason_is_not_executed():
    assert posting_observation_blocker({"status": "unavailable", "reason_code": []}) is None


@pytest.mark.parametrize("code", ["fetch_timeout", "source_gated", "unknown_schema"])
def test_inconclusive_recheck_does_not_erase_previous_dead_route(review_service, code):
    record_observation(review_service)
    record_observation(review_service, code)
    assert review_service.detail("example-job", T)["package_blocker"]["reason_code"] == "posting_unavailable"
    assert review_service.store.latest_form_inspection("example-job", T)["reason_code"] == code
    record_observation(review_service, "public_schema_inspected", "inspected")
    assert review_service.detail("example-job", T)["package_blocker"] is None


def test_negative_observation_does_not_override_historical_receipt(review_service):
    record_observation(review_service)
    with sqlite3.connect(review_service.store.database_path) as db:
        db.execute("UPDATE applications SET status='submitted'")
    assert review_service.overview(T)["items"][0]["status"] == "submitted"
