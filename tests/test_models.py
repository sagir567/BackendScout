from backend_scout.models import ApplicationStatus, Job


def test_application_statuses_include_approval_gates() -> None:
    assert ApplicationStatus.APPROVED_TO_TAILOR.value == "approved_to_tailor"
    assert ApplicationStatus.APPROVED_TO_SUBMIT.value == "approved_to_submit"
    assert ApplicationStatus.SUBMITTED.value == "submitted"


def test_job_match_score_is_bounded() -> None:
    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs.",
        match_score=92,
    )

    assert job.match_score == 92
