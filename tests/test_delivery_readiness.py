from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import backend_scout.delivery_readiness as readiness
from backend_scout.models import Job
from backend_scout.portal_adapters import adapter_for_url

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
FINGERPRINT = "a" * 64
URL = "https://jobs.ashbyhq.com/synthetic/backend/application"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return NOW

    monkeypatch.setattr(readiness, "datetime", FixedDateTime)


@pytest.fixture
def job():
    return Job(
        source="fixture",
        source_url="https://source.example.test/jobs/backend",
        application_url=URL,
        company="Synthetic Company",
        title="Backend Engineer",
        description=(
            "Build and test Python backend APIs, maintain PostgreSQL services, "
            "and collaborate on reliable deployments with the engineering team."
        ),
        match_score=100,
    )


@pytest.fixture
def inspection():
    return {
        "kind": "browser_form",
        "passed": True,
        "application_url": URL,
        "package_fingerprint": FINGERPRINT,
        "adapter": "ashby",
        "blockers": [],
        "checked_at": NOW.isoformat(),
    }


def assess(job, **overrides):
    arguments = {
        "lifecycle_status": "review_ready",
        "has_cv": True,
        "package_ready": True,
        "package_fingerprint": FINGERPRINT,
    }
    arguments.update(overrides)
    return readiness.assess_delivery_readiness(job, **arguments)


def test_result_is_frozen_and_serializable(job, inspection):
    result = assess(job, inspection=inspection)
    assert isinstance(result, readiness.DeliveryReadiness)
    assert result.model_dump() == {
        "state": "supported_after_approval",
        "label": "Supported after approval",
        "reasons": ["current_browser_form_inspection", "approval_required"],
        "adapter": "ashby",
        "route": "portal",
        "checked_at": NOW.isoformat(),
    }
    with pytest.raises(ValidationError):
        result.state = "unavailable"
    assert readiness.DeliveryReadiness.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "probably_supported"),
        ("route", "email"),
        ("probability", 100),
    ],
)
def test_schema_does_not_add_routes_or_probabilities(job, field, value):
    payload = assess(job).model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        readiness.DeliveryReadiness.model_validate(payload)


@pytest.mark.parametrize(
    "url,adapter",
    [
        (URL, "ashby"),
        ("https://boards.greenhouse.io/synthetic/jobs/123", "greenhouse"),
        ("https://www.comeet.com/jobs/synthetic/123", "comeet"),
        ("https://jobs.lever.co/synthetic/123", "lever"),
    ],
)
def test_host_registry_is_not_inspection(job, url, adapter):
    result = assess(job.model_copy(update={"application_url": url}))
    assert result.state == "unverified"
    assert result.adapter == adapter
    assert result.reasons == ["inspection_required"]
    assert result.checked_at is None


@pytest.mark.parametrize(
    "url,adapter",
    [
        (URL, "ashby"),
        ("https://boards.greenhouse.io/synthetic/jobs/123", "greenhouse"),
        ("https://www.comeet.com/jobs/synthetic/123", "comeet"),
        ("https://jobs.lever.co/synthetic/123", "lever"),
    ],
)
def test_current_production_adapter_accepts_only_its_bound_inspection(
    job, inspection, url, adapter
):
    inspection.update(application_url=url, adapter=adapter)
    result = assess(job.model_copy(update={"application_url": url}), inspection=inspection)
    assert result.state == "supported_after_approval"
    assert result.adapter == adapter


@pytest.mark.parametrize(
    "url",
    [
        "https://ashbyhq.com.evil.test/jobs/1",
        "https://notashbyhq.com/jobs/1",
        "https://greenhouse.io.evil.test/jobs/1",
        "https://notlever.co/jobs/1",
        "https://careers.example.test/jobs/ashbyhq.com",
        "https://careers.example.test/jobs/1?next=https://jobs.ashbyhq.com/synthetic/1",
        "https://careers.example.test/jobs/1",
        "https://example.com/jobs/1",
        "https://localhost/jobs/1",
        "https://127.0.0.1/jobs/1",
    ],
)
def test_false_positive_hosts_and_test_adapters_never_become_supported(job, inspection, url):
    inspection.update(application_url=url)
    result = assess(job.model_copy(update={"application_url": url}), inspection=inspection)
    assert result.state == "manual_or_unsupported"
    assert result.adapter is None
    assert result.checked_at is None


@pytest.mark.parametrize("host", ["linkedin.com", "www.linkedin.com", "il.linkedin.com"])
def test_linkedin_is_manual_even_with_registered_adapter_and_inspection(job, inspection, host):
    url = f"https://{host}/jobs/view/123"
    assert adapter_for_url(url).name == "linkedin-easy-apply"
    inspection.update(application_url=url, adapter="linkedin-easy-apply")
    result = assess(job.model_copy(update={"application_url": url}), inspection=inspection)
    assert result.state == "manual_or_unsupported"
    assert result.adapter == "linkedin-easy-apply"
    assert result.checked_at is None


@pytest.mark.parametrize(
    "status",
    [
        "submitted",
        "closed",
        "rejected",
        "offer",
        "assessment",
        "interview",
        "recruiter_reply",
        "submission_unknown",
        "unknown",
        "sending",
        "unresolved",
        "historical",
        "uncertain",
        "submission_prepared",
        "awaiting_human_verification",
        "new_future_status",
        "",
        "SUBMITTED",
    ],
)
def test_historical_or_unresolved_lifecycle_is_unavailable_even_with_valid_inspection(
    job,
    inspection,
    status,
):
    result = assess(job, lifecycle_status=status, inspection=inspection)
    assert result.state == "unavailable"
    assert result.reasons == ["lifecycle_unavailable"]
    assert result.checked_at is None


@pytest.mark.parametrize("code", ["external_receipt_check", "legacy_portal_incomplete"])
def test_unresolved_transmission_cannot_hide_behind_active_lifecycle(job, inspection, code):
    assert assess(job, needs_input_code=code, inspection=inspection).state == "unavailable"


@pytest.mark.parametrize("status", ["discovered", "review_ready", "approved", "preparing"])
def test_supported_state_never_implies_approval(job, inspection, status):
    result = assess(job, lifecycle_status=status, inspection=inspection)
    assert result.state == "supported_after_approval"
    assert "approval_required" in result.reasons


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://jobs.ashbyhq.com/synthetic/backend/application",
        "javascript:alert(1)",
        "file:///private/cv.pdf",
        "https://user:secret@jobs.ashbyhq.com/synthetic/1",
        "https://jobs.ashbyhq.com/login",
        "https://jobs.ashbyhq.com/synthetic/1?access_token=secret",
        "https://jobs.ashbyhq.com/synthetic/1?code=secret",
        "https://jobs.ashbyhq.com:invalid/synthetic/1",
        "https://jobs.ashbyhq.com:99999/synthetic/1",
        "https://[invalid",
        "https://jobs.ashbyhq.com/synthetic/with space",
    ],
)
def test_missing_or_unsafe_official_url_needs_input_without_source_fallback(job, inspection, url):
    job = job.model_copy(update={"application_url": url, "source_url": URL})
    result = assess(job, inspection=inspection)
    assert result.state == "needs_input"
    assert "official_route_required" in result.reasons
    assert result.checked_at is None


@pytest.mark.parametrize(
    "posting",
    [
        "Backend Engineer",
        "Short summary.",
        "Published Lin-Srael job listing.",
        "Published public ATS job posting.",
        "Indeed email recommendation.",
        "A" * 59,
    ],
)
def test_absent_full_posting_cannot_be_overridden_by_inspection(job, inspection, posting):
    job = job.model_copy(update={"description": posting})
    result = assess(job, inspection=inspection)
    assert result.state == "needs_input"
    assert "full_posting_required" in result.reasons


def test_long_title_is_still_not_a_full_posting(job, inspection):
    title = "Backend Engineer for Distributed Event Processing and Database Infrastructure"
    job = job.model_copy(update={"description": title, "title": title})
    result = assess(job, inspection=inspection)
    assert result.state == "needs_input"
    assert "full_posting_required" in result.reasons


@pytest.mark.parametrize(
    "flag,reason", [("has_cv", "cv_required"), ("package_ready", "package_required")]
)
@pytest.mark.parametrize("value", [False, None, 0, 1, "false", "true", [], {}])
def test_current_cv_and_package_require_actual_true_bools(job, inspection, flag, reason, value):
    result = assess(job, inspection=inspection, **{flag: value})
    assert result.state == "needs_input"
    assert reason in result.reasons
    assert result.checked_at is None


def test_all_missing_prerequisites_are_explicit(job, inspection):
    job = job.model_copy(update={"application_url": None, "description": "Backend Engineer"})
    result = assess(job, has_cv=False, package_ready=False, inspection=inspection)
    assert result.reasons == [
        "official_route_required",
        "full_posting_required",
        "cv_required",
        "package_required",
    ]


@pytest.mark.parametrize(
    "code", ["required_answers", "source_login_required", "full_posting_required"]
)
def test_explicit_input_request_cannot_be_overridden_by_inspection(job, inspection, code):
    result = assess(job, needs_input_code=code, inspection=inspection)
    assert result.state == "needs_input"
    assert code in result.reasons


@pytest.mark.parametrize("status", ["needs_input", "blocked"])
def test_unresolved_input_lifecycle_needs_input_even_without_code(job, inspection, status):
    result = assess(job, lifecycle_status=status, inspection=inspection)
    assert result.state == "needs_input"
    assert result.reasons == ["input_required"]


@pytest.mark.parametrize("score", [None, 0, 35, 85, 100])
@pytest.mark.parametrize("has_inspection", [False, True])
def test_match_score_and_professional_assessment_do_not_affect_delivery(
    job,
    inspection,
    score,
    has_inspection,
):
    changed = job.model_copy(
        update={
            "match_score": score,
            "match_reason": "Not a professional match.",
            "required_skills": ["UnrelatedSkill"],
            "years_experience": "20+ years",
        }
    )
    evidence = inspection if has_inspection else None
    assert assess(changed, inspection=evidence) == assess(job, inspection=evidence)


@pytest.mark.parametrize("value", [False, 0, 1, "true", "false", "yes", None, [], {}])
def test_inspection_passed_is_never_coerced_to_bool(job, inspection, value):
    inspection["passed"] = value
    result = assess(job, inspection=inspection)
    assert result.state == "unverified"
    assert result.checked_at is None


@pytest.mark.parametrize(
    "key",
    [
        "kind",
        "passed",
        "application_url",
        "package_fingerprint",
        "adapter",
        "blockers",
        "checked_at",
    ],
)
def test_each_inspection_fact_is_required(job, inspection, key):
    inspection.pop(key)
    assert assess(job, inspection=inspection).state == "unverified"


@pytest.mark.parametrize(
    "key,value",
    [
        ("kind", "host_registry"),
        ("kind", "email"),
        ("kind", None),
        ("application_url", URL + "/"),
        ("application_url", URL.replace("/backend/", "/different-job/")),
        ("application_url", URL + "?ref=different"),
        ("application_url", URL.replace("https:", "http:")),
        ("package_fingerprint", "b" * 64),
        ("package_fingerprint", None),
        ("adapter", "lever"),
        ("adapter", "removed-adapter"),
        ("adapter", None),
        ("blockers", ["captcha"]),
        ("blockers", [""]),
        ("blockers", False),
        ("blockers", ""),
        ("blockers", {}),
        ("blockers", None),
        ("blockers", ()),
    ],
)
def test_inspection_binding_and_shape_fail_closed(job, inspection, key, value):
    inspection[key] = value
    assert assess(job, inspection=inspection).state == "unverified"


@pytest.mark.parametrize("fingerprint", [None, "", "  ", "b" * 64, False, 1])
def test_current_package_fingerprint_is_required(job, inspection, fingerprint):
    assert assess(job, inspection=inspection, package_fingerprint=fingerprint).state == "unverified"


def test_omitted_optional_fingerprint_never_reuses_the_inspection_fingerprint(job, inspection):
    result = readiness.assess_delivery_readiness(
        job,
        lifecycle_status="review_ready",
        has_cv=True,
        package_ready=True,
        inspection=inspection,
    )
    assert result.state == "unverified"
    assert result.reasons == ["inspection_package_mismatch"]


@pytest.mark.parametrize("fingerprint", [None, "", "  ", False, 1])
def test_two_matching_missing_or_malformed_fingerprints_are_not_evidence(
    job, inspection, fingerprint
):
    inspection["package_fingerprint"] = fingerprint
    assert assess(job, inspection=inspection, package_fingerprint=fingerprint).state == "unverified"


@pytest.mark.parametrize("value", [False, "passed", [], 1, {}])
def test_malformed_inspection_is_unverified(job, value):
    assert assess(job, inspection=value).state == "unverified"


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        False,
        1,
        NOW,
        "",
        "not-a-time",
        "2026-02-30T12:00:00Z",
        "2026-11-03",
        "2026-11-03T12:00:00",
        "2026-11-03T14:00:00+02:00",
    ],
)
def test_inspection_timestamp_must_be_explicit_valid_utc(job, inspection, timestamp):
    inspection["checked_at"] = timestamp
    result = assess(job, inspection=inspection)
    assert result.state == "unverified"
    assert result.checked_at is None


@pytest.mark.parametrize(
    "age,expected",
    [
        (timedelta(0), "supported_after_approval"),
        (timedelta(hours=24), "supported_after_approval"),
        (timedelta(hours=24, microseconds=1), "unverified"),
        (timedelta(days=2), "unverified"),
        (timedelta(microseconds=-1), "unverified"),
        (timedelta(days=-1), "unverified"),
    ],
)
def test_inspection_freshness_is_bounded_in_both_directions(job, inspection, age, expected):
    inspection["checked_at"] = (NOW - age).isoformat()
    assert assess(job, inspection=inspection).state == expected


def test_utc_z_timestamp_is_accepted_and_normalized(job, inspection):
    inspection["checked_at"] = "2026-11-03T12:00:00Z"
    result = assess(job, inspection=inspection)
    assert result.state == "supported_after_approval"
    assert result.checked_at == NOW.isoformat()


def test_inspection_freshness_is_not_approval_or_job_expiry(job, inspection):
    old = NOW - timedelta(days=365)
    job = job.model_copy(update={"discovered_at": old, "updated_at": old})
    inspection["approval_expires_at"] = old.isoformat()
    assert assess(job, lifecycle_status="approved", inspection=inspection).state == (
        "supported_after_approval"
    )
    inspection["checked_at"] = old.isoformat()
    inspection["approval_expires_at"] = (NOW + timedelta(days=365)).isoformat()
    assert assess(job, lifecycle_status="approved", inspection=inspection).state == "unverified"


def test_adapter_must_still_be_supported_at_assessment(job, inspection, monkeypatch):
    monkeypatch.setattr(readiness, "adapter_for_url", lambda *args, **kwargs: None)
    assert assess(job, inspection=inspection).state == "manual_or_unsupported"


def test_classifier_does_not_mutate_supplied_facts_or_reuse_reason_lists(job, inspection):
    original_job = job.model_dump()
    original_inspection = deepcopy(inspection)
    assess(job, inspection=inspection)
    first = assess(job)
    first.reasons.append("local_annotation")
    assert "local_annotation" not in assess(job).reasons
    assert job.model_dump() == original_job
    assert inspection == original_inspection
