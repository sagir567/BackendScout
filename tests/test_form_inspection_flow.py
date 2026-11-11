import json
import socket
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from backend_scout.application_routes import ApplicationRoute
from backend_scout.application_store import SCHEMA_VERSION, ApplicationConflict, ApplicationStore
from backend_scout.config import TrackerName
from backend_scout.task_queue import QueuedTaskKind, QueuedTaskStatus, list_tasks, mark_task_status

T = TrackerName.TEST


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("No real network calls"))


@pytest.fixture
def hybrid(review_service, monkeypatch):
    service = review_service
    service.settings.portal_delivery_mode = "hybrid"
    job = service.store.get_application("example-job", T).job
    route = ApplicationRoute(
        status="verified", reason_code="route_verified", message="Verified actual employer form.",
        source_url=job.source_url, company=job.company, title=job.title,
        application_url="https://careers.example.test/jobs/123/apply",
        employer_posting_url=job.application_url, adapter_name="employer-form",
        evidence_urls=[job.application_url, "https://careers.example.test/jobs/123/apply"],
    )
    result = {"route": route.model_dump(mode="json"), "questions": [],
              "proposed_answers": {"review.form_schema_sha256": "1" * 64},
              "form_identified": True, "field_signature": "fixture-field-signature"}
    calls = []

    def inspector(job, tracker):
        calls.append((job, tracker))
        return result

    service.browser_inspector = inspector
    service.route_checker = lambda *_: pytest.fail("Hybrid must not invoke the legacy HTTP checker")
    monkeypatch.setattr("backend_scout.greenhouse_forms.inspect_greenhouse_form",
                        lambda *_: pytest.fail("Hybrid must not invoke public HTTP inspection"))
    service.drafter = lambda *_: None
    return service, result, calls


def greenhouse_job(service):
    job = service.store.get_application("example-job", T).job.model_copy(
        update={"application_url": "https://job-boards.greenhouse.io/example/jobs/123"})
    service.store.revise_job("example-job", T, job)
    return job


def test_registry_support_is_not_a_ready_promise(review_service):
    detail = review_service.detail("example-job", T)
    assert detail["item"]["delivery_readiness"]["state"] == "unverified"
    assert detail["item"]["delivery_readiness"]["adapter"] == "ashby"
    assert detail["package"]["approved"] is False
    assert review_service.overview(T)["items"][0]["delivery_readiness"]["state"] == "unverified"
    assert review_service.overview(T)["counts"]["agent_apply"] == 1
    assert not list_tasks(review_service.store.database_path)


def test_public_check_is_queued_deduplicated_and_not_an_approval(review_service):
    greenhouse_job(review_service)
    result = review_service.request_form_inspection("example-job", T, "12345")
    again = review_service.request_form_inspection("example-job", T, "12345")
    assert result.task_id == again.task_id
    tasks = list_tasks(review_service.store.database_path)
    assert len(tasks) == 1 and tasks[0].kind == QueuedTaskKind.PACKAGE_FORM_INSPECT
    assert review_service.detail("example-job", T)["form_check_pending"]
    assert review_service.store.get_application("example-job", T).current_fingerprint is None
    with pytest.raises(PermissionError):
        review_service.request_form_inspection("example-job", T, "unauthorized")


def test_worker_observation_is_persisted_without_cv_or_delivery_changes(review_service, monkeypatch):
    job = greenhouse_job(review_service)

    @dataclass(frozen=True)
    class Observation:
        status: str = "inspected"
        reason_code: str = "public_form_inspected"
        message: str = "Public fields observed; upload and delivery are not tested."
        source_url: str = "https://boards-api.greenhouse.io/v1/boards/example/jobs/123?questions=true"
        checked_at: str = datetime.now(UTC).isoformat()
        questions: tuple = ()

    monkeypatch.setattr("backend_scout.greenhouse_forms.inspect_greenhouse_form", lambda _: Observation())
    before = review_service.store.get_application("example-job", T)
    outcome = review_service.inspect_form("example-job", T, "12345")
    assert outcome.status == "succeeded"
    after = review_service.store.get_application("example-job", T)
    assert (before.status, before.current_fingerprint) == (after.status, after.current_fingerprint)
    assert review_service.store.latest_form_inspection("example-job", T)["status"] == "inspected"
    assert review_service.detail("example-job", T)["item"]["delivery_readiness"]["state"] == "unverified"
    review_service.store.revise_job(
        "example-job", T, job.model_copy(update={"application_url": job.application_url.replace("123", "456")}))
    assert review_service.store.latest_form_inspection("example-job", T) is None
    with pytest.raises(ApplicationConflict, match="destination changed"):
        review_service.store.save_form_inspection("example-job", T, job.application_url, {})


def test_schema_three_upgrade_keeps_current_packages_and_creates_inspection_table(review_service):
    store = review_service.store
    with sqlite3.connect(store.database_path) as db:
        db.execute("DROP TABLE application_form_inspections")
        for table in ("application_routes", "browser_observations", "browser_budget_reservations",
                      "browser_budget_actions", "browser_budget_external_usage",
                      "submission_notification_outbox"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("ALTER TABLE application_metadata DROP COLUMN proposed_answers_json")
        db.execute("DELETE FROM application_schema_versions WHERE version>=4")
    upgraded = ApplicationStore(store.database_path, store.private_root)
    assert upgraded.get_application("example-job", T).current_fingerprint is None
    assert upgraded.latest_form_inspection("example-job", T) is None
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT MAX(version) FROM application_schema_versions").fetchone()[0] == SCHEMA_VERSION


def test_unsupported_and_historical_checks_never_enqueue(review_service):
    assert review_service.request_form_inspection("example-job", T, "12345").status == "blocked"
    greenhouse_job(review_service)
    with sqlite3.connect(review_service.store.database_path) as db:
        db.execute("UPDATE applications SET status='submitted'")
    assert review_service.request_form_inspection("example-job", T, "12345").status == "blocked"
    assert review_service.overview(T)["counts"]["agent_apply"] == 0
    assert not list_tasks(review_service.store.database_path)


def test_hybrid_full_posting_can_draft_without_a_verified_delivery_form(hybrid):
    service, _, calls = hybrid
    first = service.request_tailoring("example-job", T, "12345")
    again = service.request_tailoring("example-job", T, "12345")
    assert first.task_id and again.reason_code == "queued" and not calls
    task, = list_tasks(service.store.database_path)
    assert task.kind == QueuedTaskKind.PACKAGE_CV_DRAFT
    assert service.draft("example-job", T).reason_code == "cv_ready_route_pending"
    detail = service.detail("example-job", T)
    assert detail["item"]["has_cv"]
    assert detail["package"] is None
    assert service.approve_and_queue("example-job", T, "12345", "0" * 64).reason_code == "browser_preflight_required"


def test_hybrid_worker_chains_authorized_tailoring_and_preserves_schema_answer(hybrid):
    service, result, calls = hybrid
    queued = service.request_form_inspection("example-job", T, "12345", tailor_after=True)
    outcome = service.inspect_form("example-job", T, "12345", tailor_after=True)
    assert outcome.reason_code == "queued" and len(calls) == 1
    mark_task_status(queued.task_id, QueuedTaskStatus.DONE, path=service.store.database_path)
    assert service.draft("example-job", T).reason_code == "review_ready"
    package = service.preview_package("example-job", T)[0]
    assert {a.key: a.value for a in package.answers}["review.form_schema_sha256"] == "1" * 64
    assert package.official_destination == result["route"]["application_url"]
    assert {task.kind for task in list_tasks(service.store.database_path)} == {
        QueuedTaskKind.PACKAGE_FORM_INSPECT, QueuedTaskKind.PACKAGE_CV_DRAFT,
    }
    detail = service.detail("example-job", T)
    assert detail["delivery_mode"] == "hybrid"
    assert detail["item"]["verified_route"]["application_url"] == package.official_destination
    assert detail["item"]["browser_observation"]["phase"] == "preflight"
    assert detail["browser_observation"]["details"]["field_signature"] == result["field_signature"]
    assert detail["item"]["delivery_readiness"]["state"] != "supported_after_approval"


def test_hybrid_inspection_without_tailor_after_does_not_queue_paid_draft(hybrid):
    service, _, calls = hybrid
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    assert len(calls) == 1 and not list_tasks(service.store.database_path)


def test_ready_workspace_binds_current_pdf_to_verified_form_without_approval(hybrid):
    service, _, _ = hybrid
    service.settings.web_ready_to_apply = True
    assert service.overview(T)["items"][0]["workspace_bucket"] == "internal"
    assert service.inspect_form("example-job", T, "12345", tailor_after=True).reason_code == "queued"
    assert service.draft("example-job", T).reason_code == "review_ready"
    detail = service.detail("example-job", T)
    assert detail["item"]["workspace_bucket"] == "ready"
    assert detail["package"]["approved"] is False
    service.settings.web_allow_delivery = False
    assert service.overview(T)["items"][0]["workspace_bucket"] == "internal"


def test_hybrid_required_questions_block_until_confirmed_without_being_erased(hybrid):
    service, result, _ = hybrid
    result["questions"] = [{"key": "work_authorization", "label": "Work authorization?",
                             "required": True, "sensitive": True, "options": ["yes", "no"]}]
    result["proposed_answers"]["work_authorization"] = "yes"
    outcome = service.inspect_form("example-job", T, "12345", tailor_after=True)
    assert outcome.reason_code == "queued"
    assert service.draft("example-job", T).reason_code == "portal_input_required"
    fp = service.preview_package("example-job", T)[0].fingerprint()
    assert service.approve_and_queue("example-job", T, "12345", fp).status == "blocked"
    assert service.detail("example-job", T)["questions"][0]["sensitive"] is True
    service.update_answers("example-job", T, "12345", {"work_authorization": "yes"})
    assert service.detail("example-job", T)["questions"][0]["key"] == "work_authorization"
    updated = service.preview_package("example-job", T)[0].fingerprint()
    assert updated != fp
    assert service.approve_and_queue("example-job", T, "12345", updated).reason_code == "queued"


def test_hybrid_draft_does_not_erase_questions_discovered_after_queueing(hybrid):
    service, _, _ = hybrid
    service.inspect_form("example-job", T, "12345", tailor_after=True)
    question = {"key": "salary", "label": "Expected salary", "required": True}
    service.store.set_questions("example-job", T, [question])
    assert service.draft("example-job", T).reason_code == "portal_input_required"
    assert service.detail("example-job", T)["questions"] == [question]
    assert service.store.get_application("example-job", T).status == "needs_input"


def test_known_identity_and_optional_fields_do_not_invent_missing_input(hybrid):
    service, result, _ = hybrid
    result["questions"] = [
        {"key": "first_name", "label": "First name", "required": True},
        {"key": "email", "label": "Email", "required": True},
        {"key": "website", "label": "Website", "required": False},
    ]
    assert service.inspect_form("example-job", T, "12345", tailor_after=True).reason_code == "queued"


@pytest.mark.parametrize("status,reason", [("unavailable", "closed"), ("needs_input", "authentication_required")])
def test_hybrid_unavailable_route_persists_clear_blocker_and_never_drafts(hybrid, status, reason):
    service, result, _ = hybrid
    result["route"].update(status=status, reason_code=reason, message="Posting needs owner attention.", application_url=None)
    assert service.inspect_form("example-job", T, "12345", tailor_after=True).status == "blocked"
    assert service.store.latest_application_route("example-job", T)["status"] == status
    assert service.detail("example-job", T)["package_blocker"]["reason_code"] == reason
    assert not list_tasks(service.store.database_path)


def test_hybrid_route_and_answer_changes_revoke_approval_without_regenerating_cv(hybrid):
    service, result, _ = hybrid
    service.inspect_form("example-job", T, "12345")
    package, path = service.preview_package("example-job", T)
    pdf = path.read_bytes()
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    service.drafter = lambda *_: pytest.fail("Material preflight changes must reuse the exact PDF")
    result["route"]["application_url"] = "https://careers.example.test/jobs/123/new-form"
    result["proposed_answers"]["review.form_schema_sha256"] = "2" * 64
    assert service.inspect_form("example-job", T, "12345", tailor_after=True).reason_code == "review_ready"
    assert not service.store.package_approved("example-job", T, package.fingerprint())
    updated, updated_path = service.preview_package("example-job", T)
    assert updated.fingerprint() != package.fingerprint()
    assert updated.cv_version == package.cv_version and updated_path.read_bytes() == pdf
    assert not any(task.kind == QueuedTaskKind.PACKAGE_CV_DRAFT for task in list_tasks(service.store.database_path))
    with pytest.raises(ApplicationConflict):
        service.queue_delivery("example-job", T, "12345", package.fingerprint())


def test_hybrid_unchanged_or_timestamp_only_preflight_preserves_exact_approval(hybrid):
    service, result, _ = hybrid
    service.inspect_form("example-job", T, "12345")
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    result["route"]["checked_at"] = datetime.now(UTC).isoformat()
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    assert service.store.package_approved("example-job", T, package.fingerprint())
    inspection = service.store.latest_form_inspection("example-job", T)
    assert inspection["kind"] == "browser_form" and inspection["passed"] is True
    assert inspection["package_fingerprint"] == package.fingerprint()
    assert service.detail("example-job", T)["item"]["delivery_readiness"]["state"] == "supported_after_approval"


def test_route_alone_and_unidentified_form_do_not_claim_browser_readiness(hybrid):
    service, result, _ = hybrid
    service.inspect_form("example-job", T, "12345")
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    result["form_identified"] = False
    outcome = service.inspect_form("example-job", T, "12345", tailor_after=True)
    assert outcome.status == "blocked" and outcome.reason_code == "browser_form_unidentified"
    assert service.store.latest_form_inspection("example-job", T)["passed"] is False
    assert service.detail("example-job", T)["item"]["verified_route"] is None
    assert service.detail("example-job", T)["item"]["delivery_readiness"]["state"] != "supported_after_approval"
    assert service.approve_and_queue("example-job", T, "12345", package.fingerprint()).status == "blocked"
    assert not any(task.kind == QueuedTaskKind.PACKAGE_CV_DRAFT for task in list_tasks(service.store.database_path))


def test_hybrid_expired_route_does_not_block_cv_but_still_blocks_approval(hybrid):
    service, result, _ = hybrid
    result["route"]["checked_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    service.inspect_form("example-job", T, "12345")
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    task, = list_tasks(service.store.database_path)
    assert task.kind == QueuedTaskKind.PACKAGE_CV_DRAFT
    assert service.approve_and_queue("example-job", T, "12345", "0" * 64).status == "blocked"


def test_hybrid_failed_inspection_cannot_reuse_old_verified_observation(hybrid):
    service, _, _ = hybrid
    service.inspect_form("example-job", T, "12345")

    def fail(*_):
        raise OSError("synthetic provider secret")

    service.browser_inspector = fail
    outcome = service.inspect_form("example-job", T, "12345", tailor_after=True)
    assert outcome.status == "retryable_failure"
    assert "secret" not in json.dumps(service.detail("example-job", T))
    assert service.detail("example-job", T)["item"]["verified_route"] is None
    assert service.approve_and_queue("example-job", T, "12345", "0" * 64).status == "blocked"


@pytest.mark.parametrize("damage", ["company", "source", "questions", "proposals"])
def test_hybrid_malformed_or_wrong_job_preflight_cannot_create_review_authority(hybrid, damage):
    service, result, _ = hybrid
    if damage == "company":
        result["route"]["company"] = "Other employer"
    elif damage == "source":
        result["route"]["source_url"] = "https://other.example.test/job"
    elif damage == "questions":
        result["questions"] = [{"key": "unsafe"}]
    else:
        result["proposed_answers"] = {"password": "never store"}
    assert service.inspect_form("example-job", T, "12345").status == "retryable_failure"
    assert service.store.latest_application_route("example-job", T) is None
    assert service.store.get_application("example-job", T).current_fingerprint is None


def test_hybrid_missing_requirements_are_resolved_only_by_inspector_posting_text(hybrid):
    service, result, _ = hybrid
    job = service.store.get_application("example-job", T).job
    service.store.revise_job("example-job", T, job.model_copy(update={"description": "Published Lin-Srael job listing."}))
    result["route"]["posting_text"] = job.description
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    assert service.inspect_form("example-job", T, "12345", tailor_after=True).reason_code == "queued"
    assert service.store.get_application("example-job", T).job.description == job.description
    assert all(task.kind != QueuedTaskKind.PACKAGE_ENRICH for task in list_tasks(service.store.database_path))


def test_company_posting_to_comeet_updates_canonical_destination_and_preserves_cv(hybrid):
    service, result, _ = hybrid
    job = service.store.get_application("example-job", T).job
    company_url = "https://careers.example.test/jobs/123"
    service.store.revise_job("example-job", T, job.model_copy(update={"application_url": company_url}))
    comeet_url = "https://www.comeet.com/jobs/Example/AB.123/Backend/CC.456"
    result["route"].update(application_url=comeet_url, employer_posting_url=company_url,
                            adapter_name="comeet", evidence_urls=[company_url, comeet_url])
    before = service.cv_artifact("example-job", T).read_bytes()
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    record = service.store.get_application("example-job", T)
    assert record.job.application_url == comeet_url and record.job.source_url == job.source_url
    package = service.preview_package("example-job", T)[0]
    assert package.official_destination == comeet_url
    assert service.approve_and_queue("example-job", T, "12345", package.fingerprint()).reason_code == "queued"
    assert service.store.get_current("example-job", T).package == package
    assert service.cv_artifact("example-job", T).read_bytes() == before
    inspection = service.store.latest_form_inspection("example-job", T)
    assert inspection["job_application_url"] == comeet_url
    assert inspection["route"]["employer_posting_url"] == company_url


def test_true_posting_text_refresh_rescores_without_rewriting_cv(hybrid):
    service, result, _ = hybrid
    result["route"]["posting_text"] = "Updated requirements: " + service.store.get_application("example-job", T).job.description
    scored = []

    def scorer(job):
        scored.append(job)
        return job.model_copy(update={"match_score": 92})

    service.job_scorer = scorer
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    assert len(scored) == 1
    assert service.store.get_application("example-job", T).job.match_score == 92
    assert not any(t.kind == QueuedTaskKind.PACKAGE_CV_DRAFT for t in list_tasks(service.store.database_path))


@pytest.mark.parametrize("schema_hash", [None, "a" * 63, "g" * 64])
def test_identified_form_without_valid_schema_hash_cannot_chain_or_approve(hybrid, schema_hash):
    service, result, _ = hybrid
    if schema_hash is None:
        result["proposed_answers"].pop("review.form_schema_sha256")
    else:
        result["proposed_answers"]["review.form_schema_sha256"] = schema_hash
    outcome = service.inspect_form("example-job", T, "12345", tailor_after=True)
    assert outcome.status == "blocked" and outcome.reason_code == "form_schema_unverified"
    assert service._verified_route("example-job", T) is None
    assert service.approve_and_queue("example-job", T, "12345", "0" * 64).status == "blocked"
    assert not list_tasks(service.store.database_path)


def test_trusted_browser_form_signal_is_accepted_without_form_identified_key(hybrid):
    service, result, _ = hybrid
    result.pop("form_identified")
    result.update(kind="browser_form", passed=True)
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    assert service._verified_route("example-job", T) is not None


def test_observed_schema_context_must_match_current_proposals(hybrid):
    service, _, _ = hybrid
    service.inspect_form("example-job", T, "12345")
    with sqlite3.connect(service.store.database_path) as db:
        db.execute("UPDATE application_metadata SET proposed_answers_json=?",
                   (json.dumps({"review.form_schema_sha256": "f" * 64}),))
    assert service._verified_route("example-job", T) is None
    assert service.approve_and_queue("example-job", T, "12345", "0" * 64).status == "blocked"


def test_pending_answers_do_not_invalidate_actual_form_or_prevent_cv_preview(hybrid):
    service, result, _ = hybrid
    result["questions"] = [{"key": "privacy", "label": "Accept privacy terms", "required": True,
                             "sensitive": True, "options": ["yes", "no"]}]
    assert service.inspect_form("example-job", T, "12345").reason_code == "portal_input_required"
    inspection = service.store.latest_form_inspection("example-job", T)
    assert inspection["form_identified"] is True and inspection["passed"] is False
    assert service._verified_route("example-job", T) is not None
    assert service.preview_package("example-job", T)[0].cv_version
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    assert service.draft("example-job", T).reason_code == "portal_input_required"


@pytest.mark.parametrize("schema_changed", [False, True])
def test_consent_is_preserved_only_for_unchanged_schema_and_pdf_is_reused(hybrid, schema_changed):
    service, result, _ = hybrid
    result["questions"] = [{"key": "privacy", "label": "Privacy: https://careers.example.test/privacy-v1",
                             "required": True, "sensitive": True, "options": ["yes", "no"]}]
    service.update_answers("example-job", T, "12345", {"privacy": "yes", "Work authorization": "Israel"})
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    package, path = service.preview_package("example-job", T)
    original_pdf = path.read_bytes()
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    service.drafter = lambda *_: pytest.fail("Consent review must not regenerate the CV")
    if schema_changed:
        result["proposed_answers"]["review.form_schema_sha256"] = "2" * 64
        result["questions"][0]["label"] = "New privacy: https://careers.example.test/privacy-v2"
    service.inspect_form("example-job", T, "12345", tailor_after=True)
    confirmed = json.loads(service.store.metadata("example-job", T)["answers_json"])
    assert confirmed["Work authorization"] == "Israel"
    assert path.read_bytes() == original_pdf
    assert not any(t.kind == QueuedTaskKind.PACKAGE_CV_DRAFT for t in list_tasks(service.store.database_path))
    if schema_changed:
        assert "privacy" not in confirmed
        assert not service.store.package_approved("example-job", T, package.fingerprint())
        preview = service.preview_package("example-job", T)[0]
        assert service.approve_and_queue("example-job", T, "12345", preview.fingerprint()).reason_code == "portal_input_required"
        service.update_answers("example-job", T, "12345", {"privacy": "yes"})
        preview = service.preview_package("example-job", T)[0]
        assert service.approve_and_queue("example-job", T, "12345", preview.fingerprint()).reason_code == "queued"
    else:
        assert confirmed["privacy"] == "yes"
        assert service.store.package_approved("example-job", T, package.fingerprint())
