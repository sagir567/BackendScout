import hashlib
import json
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from backend_scout.application_routes import ApplicationRoute
from backend_scout.application_service import build_application_service
from backend_scout.application_store import ApplicationConflict, is_ledger_owned
from backend_scout.browser_budget import BudgetExceeded
from backend_scout.config import TrackerName
from backend_scout.submission import BrowserPreparationResult
from backend_scout.task_queue import list_tasks

T = TrackerName.TEST


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("No real network calls"))


@pytest.fixture
def hybrid_ready(review_service):
    service = review_service
    service.settings.portal_delivery_mode = "hybrid"
    job = service.store.get_application("example-job", T).job
    route = ApplicationRoute(status="verified", reason_code="route_verified", message="Actual form identified.",
                             source_url=job.source_url, company=job.company, title=job.title,
                             application_url=job.application_url, evidence_urls=[job.application_url],
                             adapter_name="ashby")
    service.browser_inspector = lambda *_: {"route": route.model_dump(mode="json"),
        "questions": [], "proposed_answers": {"review.form_schema_sha256": "a" * 64},
        "form_identified": True, "field_signature": "fixture-fields"}
    assert service.inspect_form("example-job", T, "12345").status == "succeeded"
    return service


def test_imported_remote_cv_approval_never_authorizes_delivery(review_service):
    service = review_service
    package, _ = service.preview_package("example-job", T)
    with pytest.raises(ApplicationConflict, match="stale|approval"):
        service.queue_delivery("example-job", T, "12345", package.fingerprint())
    assert not is_ledger_owned(service.store.database_path, "example-job", T)


def test_preview_does_not_claim_or_mutate_review_authority(review_service):
    service = review_service
    detail = service.detail("example-job", T)
    assert detail["package"]["approved"] is False
    assert detail["item"]["years_experience"] == "2+ years"
    assert service.store.get_application("example-job", T).current_fingerprint is None


def test_exact_approval_is_idempotent_and_queues_once(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    first = service.approve_and_queue("example-job", T, "12345", fp)
    second = service.approve_and_queue("example-job", T, "12345", fp)
    assert first.task_id == second.task_id
    assert len(list_tasks(service.store.database_path)) == 1
    assert is_ledger_owned(service.store.database_path, "example-job", T)
    assert not is_ledger_owned(service.store.database_path, "example-job", TrackerName.PRODUCTION)
    with sqlite3.connect(service.store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM application_approvals").fetchone()[0] == 1


def test_unauthorized_and_stale_reviews_do_not_enqueue(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    with pytest.raises(PermissionError):
        service.approve_and_queue("example-job", T, "not-owner", fp)
    with pytest.raises(ApplicationConflict, match="stale"):
        service.approve_and_queue("example-job", T, "12345", "0" * 64)
    assert not list_tasks(service.store.database_path)


def test_changed_pdf_rejects_previous_review_before_queueing(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    service.cv_artifact("example-job", T).write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="changed"):
        service.approve_and_queue("example-job", T, "12345", fp)
    assert not list_tasks(service.store.database_path)


def test_identity_is_reviewed_and_changed_contact_details_invalidate_approval(review_service):
    service = review_service
    package = service.preview_package("example-job", T)[0]
    assert any(
        answer.key == "identity.email" and answer.value == "fixture@example.test"
        for answer in package.answers
    )
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    evidence = service.identity_loader()
    evidence.identity.email = "updated@example.test"
    with pytest.raises(ApplicationConflict):
        service.queue_delivery("example-job", T, "12345", package.fingerprint())


def test_answers_change_fingerprint_and_invalidate_approval(review_service):
    service = review_service
    old = service.preview_package("example-job", T)[0].fingerprint()
    service.approve_and_queue("example-job", T, "12345", old)
    service.update_answers("example-job", T, "12345", {"Work authorization": "Israel"})
    new = service.preview_package("example-job", T)[0].fingerprint()
    assert new != old
    with pytest.raises(ApplicationConflict):
        service.queue_delivery("example-job", T, "12345", old)


def test_passwords_are_not_stored_as_application_answers(review_service):
    with pytest.raises(ValueError, match="Credentials"):
        review_service.update_answers(
            "example-job", T, "12345", {"Portal password": "not-for-this-store"}
        )


def test_missing_full_posting_fetches_before_any_model_call(review_service):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.store.revise_job(
        "example-job", T, job.model_copy(update={"description": "Published Lin-Srael job listing."})
    )
    service.drafter = lambda *_: pytest.fail("No model call for a placeholder")
    result = service.request_tailoring("example-job", T, "12345")
    assert result.status == "succeeded"
    assert result.reason_code == "queued"
    tasks = list_tasks(service.store.database_path)
    assert len(tasks) == 1 and tasks[0].kind.value == "package_enrich"
    assert tasks[0].payload["tailor_after"] is True
    assert service.store.get_application("example-job", T).status == "enriching"


def test_existing_cv_remains_viewable_without_job_route_or_complete_description(review_service):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.store.revise_job(
        "example-job",
        T,
        job.model_copy(
            update={"application_url": None, "description": "Published Lin-Srael job listing."}
        ),
    )
    detail = service.detail("example-job", T)
    assert detail["package"] is None
    assert detail["package_blocker"]["reason_code"] == "full_posting_required"
    assert detail["item"]["has_cv"] is True
    assert detail["item"]["cv_url"]
    assert detail["item"]["cv_preview_url"]
    assert service.cv_artifact("example-job", T).is_file()


def test_fetch_only_does_not_queue_a_paid_cv_draft(review_service):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.request_refresh("example-job", T, "12345")
    result = SimpleNamespace(
        status="enriched", job=job, reason_code="full_description_found", message="Source read"
    )
    assert (
        service.refresh_details("example-job", T, "12345", enricher=lambda _: result).reason_code
        == "details_refreshed"
    )
    tasks = list_tasks(service.store.database_path)
    assert len(tasks) == 1 and tasks[0].kind.value == "package_enrich"
    assert tasks[0].payload["tailor_after"] is False


def test_source_refresh_updates_requirements_then_queues_requested_cv(review_service):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.store.revise_job(
        "example-job", T, job.model_copy(update={"description": "Published Lin-Srael job listing."})
    )
    service.request_tailoring("example-job", T, "12345")
    result = SimpleNamespace(
        status="enriched", job=job, reason_code="details_ready", message="Fetched from source"
    )
    outcome = service.refresh_details(
        "example-job", T, "12345", tailor_after=True, enricher=lambda _: result
    )
    assert outcome.reason_code == "queued"
    assert service.store.get_application("example-job", T).status == "drafting"
    assert len(list_tasks(service.store.database_path)) == 2


def test_unavailable_source_never_invents_description_or_queues_model(review_service):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.store.revise_job(
        "example-job", T, job.model_copy(update={"description": "Published Lin-Srael job listing."})
    )
    service.request_tailoring("example-job", T, "12345")
    unavailable = SimpleNamespace(
        status="unavailable",
        job=None,
        reason_code="source_login_required",
        message="The source requires sign-in; no full posting was retrieved.",
    )
    outcome = service.refresh_details(
        "example-job", T, "12345", tailor_after=True, enricher=lambda _: unavailable
    )
    assert outcome.status == "blocked"
    assert service.store.get_application("example-job", T).status == "needs_input"
    assert len(list_tasks(service.store.database_path)) == 1
    assert service.cv_artifact("example-job", T).is_file()
    assert service.detail("example-job", T)["package_blocker"]["message"] == unavailable.message


def test_cv_generation_succeeds_without_submission_url_but_applying_does_not(review_service):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.store.revise_job("example-job", T, job.model_copy(update={"application_url": None}))
    service.drafter = lambda *_: None  # The synthetic manifest already exists.
    service.request_tailoring("example-job", T, "12345")
    assert service.draft("example-job", T).reason_code == "cv_ready_route_missing"
    assert service.detail("example-job", T)["item"]["has_cv"] is True
    assert (
        service.detail("example-job", T)["package_blocker"]["reason_code"]
        == "official_route_required"
    )
    with pytest.raises(ApplicationConflict):
        service.preview_package("example-job", T)


@pytest.mark.parametrize("legacy_status", ["submitted", "closed", "submission_unknown", "rejected"])
def test_historical_and_uncertain_imports_cannot_apply_again(review_service, legacy_status):
    service = review_service
    job = service.store.get_application("example-job", T).job
    service.store.import_snapshot("example-job", T, job, legacy_status)
    with pytest.raises(ApplicationConflict):
        service.preview_package("example-job", T)
    assert not list_tasks(service.store.database_path)


def test_remote_status_cannot_override_a_new_local_approval(review_service):
    service = review_service
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    job = service.store.get_application("example-job", T).job
    service.store.import_snapshot("example-job", T, job, "found")
    assert service.store.get_application("example-job", T).status == "approved"


@pytest.mark.parametrize("external_state", ["closed", "rejected", "interview", "offer"])
def test_external_terminal_update_blocks_queued_delivery(review_service, external_state):
    service = review_service
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    job = service.store.get_application("example-job", T).job
    service.store.import_snapshot("example-job", T, job, external_state)
    service.delivery_adapter = lambda *_: pytest.fail(
        "Never replay an externally closed or advanced application"
    )
    with pytest.raises(ApplicationConflict):
        service.deliver("example-job", T, package.fingerprint(), "queued-before-external-update")


def test_unexpected_adapter_failure_becomes_unknown_not_retryable(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    service.approve_and_queue("example-job", T, "12345", fp)
    calls = []

    def fail(*_):
        calls.append(True)
        raise RuntimeError("synthetic disconnect")

    service.delivery_adapter = fail
    result = service.deliver("example-job", T, fp, "send-1")
    assert result.status == "uncertain"
    assert service.store.get_application("example-job", T).status == "submission_unknown"
    with pytest.raises(ApplicationConflict):
        service.deliver("example-job", T, fp, "send-1")
    assert len(calls) == 1


def test_reported_receipt_without_proof_is_not_marked_submitted(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    service.approve_and_queue("example-job", T, "12345", fp)
    service.delivery_adapter = lambda *_: BrowserPreparationResult("submitted", (), "Receipt")
    assert service.deliver("example-job", T, fp, "send-1").status == "uncertain"
    assert service.store.get_application("example-job", T).status == "submission_unknown"


def test_pre_submit_missing_input_is_blocked_and_unchanged_approval_can_resume(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    service.approve_and_queue("example-job", T, "12345", fp)
    service.delivery_adapter = lambda *_: BrowserPreparationResult(
        "submission_prepared", (), "Sign in in the open browser.", ("portal_authentication",)
    )
    assert service.deliver("example-job", T, fp, "send-1").status == "blocked"
    assert service.store.get_application("example-job", T).status == "needs_input"
    result = service.queue_delivery("example-job", T, "12345", fp)
    assert result.reason_code == "queued"


def test_receipt_with_saved_checksum_is_recorded(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    service.approve_and_queue("example-job", T, "12345", fp)
    proof = service.store.private_root / "proof.png"
    proof.write_bytes(b"synthetic proof fixture")
    service.delivery_adapter = lambda *_: BrowserPreparationResult(
        "submitted",
        (),
        "Application received",
        screenshot_path=str(proof),
        screenshot_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
    )
    assert service.deliver("example-job", T, fp, "send-1").status == "succeeded"
    assert service.store.get_application("example-job", T).status == "submitted"


@pytest.mark.parametrize("result_kind", ["missing_file", "invalid_result"])
def test_invalid_receipt_is_unknown_and_never_replayed(review_service, result_kind):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    service.approve_and_queue("example-job", T, "12345", fp)
    result = (
        None
        if result_kind == "invalid_result"
        else BrowserPreparationResult(
            "submitted",
            (),
            "Receipt",
            screenshot_path=str(service.store.private_root / "missing.png"),
            screenshot_sha256="0" * 64,
        )
    )
    service.delivery_adapter = lambda *_: result
    assert service.deliver("example-job", T, fp, "send-1").status == "uncertain"
    assert service.store.get_application("example-job", T).status == "submission_unknown"
    with pytest.raises(ApplicationConflict):
        service.queue_delivery("example-job", T, "12345", fp)


def test_active_draft_cannot_be_approved_or_mutated_mid_generation(review_service):
    service = review_service
    fp = service.preview_package("example-job", T)[0].fingerprint()
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    for operation in (
        lambda: service.approve_and_queue("example-job", T, "12345", fp),
        lambda: service.request_revision("example-job", T, "12345", "Change summary"),
        lambda: service.update_answers("example-job", T, "12345", {"Question": "Answer"}),
    ):
        with pytest.raises(ApplicationConflict):
            operation()
    assert len(list_tasks(service.store.database_path)) == 1


def test_builder_injects_inspector_without_starting_browser(review_service):
    review_service.settings.portal_delivery_mode = "hybrid"

    def inspector(*_):
        pytest.fail("Constructing an HTTP service cannot start browser inspection")

    service = build_application_service(review_service.settings, browser_inspector=inspector)
    assert service.browser_inspector is inspector and service.route_checker is None
    assert service.request_form_inspection("example-job", T, "12345").reason_code == "queued"


@pytest.mark.parametrize("key", ["review.form_schema_sha256", "Review.overwrite"])
def test_user_cannot_forge_reserved_review_context(hybrid_ready, key):
    service = hybrid_ready
    before = service.preview_package("example-job", T)[0]
    with pytest.raises(ValueError, match="Reserved review"):
        service.update_answers("example-job", T, "12345", {key: "forged"})
    assert service.preview_package("example-job", T)[0] == before


def test_proposed_schema_hash_cannot_be_overridden_by_historical_confirmed_answer(hybrid_ready):
    service = hybrid_ready
    with sqlite3.connect(service.store.database_path) as db:
        db.execute("UPDATE application_metadata SET answers_json=?", (json.dumps({"review.form_schema_sha256": "forged"}),))
    package = service.preview_package("example-job", T)[0]
    assert {a.key: a.value for a in package.answers}["review.form_schema_sha256"] == "a" * 64


def test_hybrid_delivery_retains_structured_questions_and_adapter_reason(hybrid_ready):
    service = hybrid_ready
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    question = {"key": "new_terms", "label": "Accept updated terms", "required": True,
                "sensitive": True, "options": ["yes", "no"]}
    calls = []

    def adapter(*_):
        calls.append(True)
        return BrowserPreparationResult("needs_input", (), "Terms changed before filling.",
                                        review_questions=(question,), reason_code="form_schema_changed")

    service.delivery_adapter = adapter
    outcome = service.deliver("example-job", T, package.fingerprint(), "hybrid-gap")
    assert outcome.reason_code == "form_schema_changed" and outcome.status == "blocked"
    assert service.detail("example-job", T)["questions"] == [question]
    assert service.queue_delivery("example-job", T, "12345", package.fingerprint()).status == "blocked"
    assert service.store.latest_delivery("example-job", T)["state"] == "not_sent"
    assert len(calls) == 1


def test_hybrid_unknown_uses_adapter_reason_and_is_never_retried(hybrid_ready):
    service = hybrid_ready
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    calls = []

    def adapter(*_):
        calls.append(True)
        return BrowserPreparationResult("submission_unknown", (), "Click outcome is unresolved.",
                                        reason_code="submit_ack_lost", receipt_details={"clicked": True})

    service.delivery_adapter = adapter
    outcome = service.deliver("example-job", T, package.fingerprint(), "hybrid-unknown")
    assert outcome.status == "uncertain" and outcome.reason_code == "submit_ack_lost"
    for operation in (
        lambda: service.deliver("example-job", T, package.fingerprint(), "hybrid-unknown"),
        lambda: service.queue_delivery("example-job", T, "12345", package.fingerprint()),
    ):
        with pytest.raises(ApplicationConflict):
            operation()
    assert len(calls) == 1
    observation = service.store.latest_browser_observation("example-job", T)
    assert observation["phase"] == "delivery"
    assert observation["details"]["receipt_details"] == {"clicked": True}


def test_hybrid_receipt_details_are_bound_to_exact_package_and_proof(hybrid_ready):
    from PIL import Image

    service = hybrid_ready
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    proof = service.store.private_root / "hybrid-receipt.png"
    Image.new("RGB", (2, 2)).save(proof)
    digest = hashlib.sha256(proof.read_bytes()).hexdigest()
    service.delivery_adapter = lambda *_: BrowserPreparationResult(
        "submitted", (), "Visible application receipt.", screenshot_path=str(proof),
        screenshot_sha256=digest, reason_code="receipt_confirmed",
        receipt_details={"confirmation_url": "https://jobs.ashbyhq.com/synthetic/thanks", "visible": True},
    )
    outcome = service.deliver("example-job", T, package.fingerprint(), "hybrid-success")
    assert outcome.reason_code == "receipt_confirmed" and outcome.status == "succeeded"
    observation = service.store.latest_browser_observation("example-job", T)
    assert observation["fingerprint"] == package.fingerprint()
    assert observation["details"]["proof_sha256"] == digest
    assert observation["details"]["cv_version"] == package.cv_version
    assert service.detail("example-job", T)["submission"]["receipt_details"]["visible"] is True


def test_material_form_change_requires_reinspection_without_cv_regeneration(hybrid_ready):
    service = hybrid_ready
    package = service.preview_package("example-job", T)[0]
    service.approve_and_queue("example-job", T, "12345", package.fingerprint())
    service.delivery_adapter = lambda *_: BrowserPreparationResult(
        "needs_input", (), "The legal links changed; reinspection is required.", reason_code="form_review_changed"
    )
    assert service.deliver("example-job", T, package.fingerprint(), "schema-changed").status == "blocked"
    assert service.queue_delivery("example-job", T, "12345", package.fingerprint()).reason_code == "browser_preflight_required"
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    preflight = next(t for t in list_tasks(service.store.database_path) if t.kind.value == "package_form_inspect")
    assert preflight.payload["tailor_after"] is True
    original = service.browser_inspector

    def changed_inspector(job, tracker):
        result = original(job, tracker)
        result["proposed_answers"]["review.form_schema_sha256"] = "b" * 64
        return result

    service.browser_inspector = changed_inspector
    assert service.inspect_form("example-job", T, "12345", tailor_after=True).reason_code == "review_ready"
    assert not service.store.package_approved("example-job", T, package.fingerprint())
    assert not any(t.kind.value == "package_cv_draft" for t in list_tasks(service.store.database_path))


@pytest.mark.parametrize("reason", ["missing_pricing", "total_monthly_limit"])
def test_cv_budget_pause_is_blocked_with_stable_reason_and_preserves_existing_pdf(hybrid_ready, reason):
    service = hybrid_ready
    path = service.cv_artifact("example-job", T)
    original = path.read_bytes()

    def paused(*_):
        raise BudgetExceeded(reason)

    service.drafter = paused
    assert service.request_tailoring("example-job", T, "12345").reason_code == "queued"
    result = service.draft("example-job", T)
    assert result.status == "blocked" and result.reason_code == reason
    assert "paused" in result.message and "existing CV is unchanged" in result.message
    assert service.store.get_application("example-job", T).status == "needs_input"
    assert service.store.metadata("example-job", T)["needs_input_code"] == reason
    assert path.read_bytes() == original
    assert service.store.attempt_count("example-job", T) == 0


def test_cv_evidence_gap_has_actionable_reason_and_preserves_existing_pdf(hybrid_ready):
    from backend_scout.cv_coverage import CvEvidenceGap, assess_cv_evidence_coverage
    from backend_scout.models import CareerEvidence

    service = hybrid_ready
    path = service.cv_artifact("example-job", T)
    original = path.read_bytes()
    evidence = CareerEvidence.model_validate({
        "identity": {"full_name": "Test Candidate", "email": "candidate@example.test"},
        "skills": [{"category": "Backend", "items": ["Python"]}],
    })
    job = service.store.get_application("example-job", T).job.model_copy(
        update={"required_skills": ["Prompting", "Evaluation"]})

    def paused(*_):
        raise CvEvidenceGap(assess_cv_evidence_coverage(evidence, job))

    service.drafter = paused
    service.request_tailoring("example-job", T, "12345")
    result = service.draft("example-job", T)
    assert result.status == "blocked" and result.reason_code == "cv_evidence_required"
    assert "Prompting, Evaluation" in result.message
    assert service.store.get_application("example-job", T).status == "needs_input"
    assert path.read_bytes() == original
    assert service.store.attempt_count("example-job", T) == 0
