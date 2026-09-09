import pytest

from backend_scout.models import (
    Application,
    ApplicationStatus,
    CvEvidenceCoverage,
    Job,
    MatchRecommendation,
    MatchResult,
    SalaryAssessment,
    ScoreBreakdownItem,
)


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


def test_match_result_requires_breakdown_total_to_equal_score() -> None:
    with pytest.raises(ValueError, match="score_breakdown"):
        MatchResult(
            match_score=50,
            recommended_action=MatchRecommendation.MAYBE,
            matched_skills=["Python"],
            missing_skills=[],
            strengths=["Strong Python overlap."],
            concerns=[],
            score_breakdown=[
                ScoreBreakdownItem(
                    component="role_relevance",
                    points_awarded=25,
                    points_max=25,
                    reason="Title match.",
                )
            ],
            salary_assessment=SalaryAssessment.UNKNOWN,
            location_assessment="unknown",
            reason_summary="Recommendation: maybe. Strong Python overlap.",
        )


def test_cv_evidence_coverage_requires_a_consistent_score() -> None:
    with pytest.raises(ValueError, match="coverage_score"):
        CvEvidenceCoverage(
            coverage_score=75,
            covered_requirements=["Python"],
            missing_requirements=["Kubernetes"],
            summary="Evidence supports one of two requirements.",
        )


def test_application_status_transition_blocks_submit_before_cv_review() -> None:
    application = Application(
        job=Job(
            source="manual",
            source_url="https://example.com/jobs/backend",
            company="Example",
            title="Backend Engineer",
            description="Build APIs.",
        ),
        status=ApplicationStatus.FOUND,
    )

    with pytest.raises(ValueError, match="found to submitted"):
        application.validate_status_transition(ApplicationStatus.SUBMITTED)


def test_application_status_transition_requires_cv_before_submit_approval() -> None:
    application = Application(
        job=Job(
            source="manual",
            source_url="https://example.com/jobs/backend",
            company="Example",
            title="Backend Engineer",
            description="Build APIs.",
        ),
        status=ApplicationStatus.APPROVED_TO_TAILOR,
    )

    with pytest.raises(ValueError, match="approved_to_tailor to approved_to_submit"):
        application.validate_status_transition(ApplicationStatus.APPROVED_TO_SUBMIT)


def test_application_status_transition_allows_submit_only_after_submit_approval() -> None:
    application = Application(
        job=Job(
            source="manual",
            source_url="https://example.com/jobs/backend",
            company="Example",
            title="Backend Engineer",
            description="Build APIs.",
        ),
        status=ApplicationStatus.APPROVED_TO_SUBMIT,
    )

    application.validate_status_transition(ApplicationStatus.SUBMITTED)


def test_application_status_transition_supports_revision_and_human_verification() -> None:
    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs.",
    )

    Application(job=job, status=ApplicationStatus.CV_DRAFTED).validate_status_transition(
        ApplicationStatus.REVISION_REQUESTED
    )
    Application(job=job, status=ApplicationStatus.REVISION_REQUESTED).validate_status_transition(
        ApplicationStatus.APPROVED_TO_TAILOR
    )
    Application(job=job, status=ApplicationStatus.SUBMISSION_PREPARED).validate_status_transition(
        ApplicationStatus.AWAITING_HUMAN_VERIFICATION
    )
    Application(job=job, status=ApplicationStatus.AWAITING_HUMAN_VERIFICATION).validate_status_transition(
        ApplicationStatus.SUBMISSION_PREPARED
    )


def test_application_status_transition_supports_assessment_after_submission() -> None:
    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs.",
    )

    Application(job=job, status=ApplicationStatus.SUBMITTED).validate_status_transition(
        ApplicationStatus.ASSESSMENT
    )
    Application(job=job, status=ApplicationStatus.ASSESSMENT).validate_status_transition(
        ApplicationStatus.INTERVIEW
    )
