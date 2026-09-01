from backend_scout.matcher import score_job
from backend_scout.models import (
    CandidateProfile,
    Job,
    LocationAssessment,
    MatchRecommendation,
    SalaryAssessment,
)


def make_profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "name": "Test Candidate",
            "target_roles": ["Backend Engineer", "Python Backend Engineer", "Software Engineer"],
            "target_locations": ["Israel", "Remote"],
            "salary_floor_nis": 15000,
            "work_preferences": {"remote": True, "hybrid": True, "onsite": False},
            "seniority": {"min_years": 0, "max_years": 3},
            "core_skills": ["Python", "FastAPI", "MongoDB", "Redis", "Docker", "Azure"],
            "nice_to_have_skills": ["Data Pipelines", "Cosmos DB", "C#"],
            "proof_points": [
                "Built Python and FastAPI backend services with Docker and Azure.",
                "Maintained MongoDB and Redis-backed backend workflows.",
            ],
            "constraints": {
                "require_truthful_cv_only": True,
                "require_approval_before_submit": True,
                "roles_to_avoid": [],
            },
        }
    )


def test_score_job_returns_apply_for_strong_backend_match() -> None:
    result = score_job(
        make_profile(),
        Job(
            source="manual",
            source_url="https://example.com/jobs/backend",
            company="Example",
            title="Backend Engineer",
            location="Tel Aviv, Israel",
            remote_policy="Hybrid, 2 days from home",
            salary_text="18,000 NIS",
            description="Build backend APIs with Python, FastAPI, Docker, and MongoDB.",
            required_skills=["Python", "FastAPI", "Docker", "MongoDB"],
            years_experience="2+ years",
        ),
    )

    assert result.recommended_action == MatchRecommendation.APPLY
    assert result.salary_assessment == SalaryAssessment.ABOVE_FLOOR
    assert result.location_assessment == LocationAssessment.FIT
    assert result.match_score >= 75
    assert "Python" in result.matched_skills


def test_score_job_downgrades_below_salary_floor_without_auto_skip() -> None:
    result = score_job(
        make_profile(),
        Job(
            source="manual",
            source_url="https://example.com/jobs/backend-low-salary",
            company="Example",
            title="Backend Engineer",
            location="Tel Aviv, Israel",
            remote_policy="Remote",
            salary_text="12,000 NIS",
            description="Build backend APIs with Python and Docker.",
            required_skills=["Python", "Docker"],
            years_experience="2 years",
        ),
    )

    assert result.salary_assessment == SalaryAssessment.BELOW_FLOOR
    assert any("below your floor" in concern for concern in result.concerns)
    assert result.recommended_action != MatchRecommendation.SKIP


def test_score_job_marks_unknown_salary_when_not_listed() -> None:
    result = score_job(
        make_profile(),
        Job(
            source="manual",
            source_url="https://example.com/jobs/backend-no-salary",
            company="Example",
            title="Backend Engineer",
            location="Remote",
            remote_policy="Remote",
            salary_text="Not listed",
            description="Build backend APIs with Python.",
            required_skills=["Python"],
            years_experience="1 year",
        ),
    )

    assert result.salary_assessment == SalaryAssessment.UNKNOWN
    assert any(item.component == "salary_fit" and item.points_awarded == 5 for item in result.score_breakdown)


def test_score_job_skips_onsite_only_role_when_onsite_is_disallowed() -> None:
    result = score_job(
        make_profile(),
        Job(
            source="manual",
            source_url="https://example.com/jobs/backend-onsite",
            company="Example",
            title="Backend Engineer",
            location="Haifa, Israel",
            remote_policy="Onsite only",
            salary_text="18,000 NIS",
            description="Build backend APIs with Python.",
            required_skills=["Python"],
            years_experience="2 years",
        ),
    )

    assert result.location_assessment == LocationAssessment.MISMATCH
    assert result.recommended_action == MatchRecommendation.SKIP


def test_score_job_handles_years_slightly_above_target_range() -> None:
    result = score_job(
        make_profile(),
        Job(
            source="manual",
            source_url="https://example.com/jobs/backend-4-years",
            company="Example",
            title="Backend Engineer",
            location="Tel Aviv, Israel",
            remote_policy="Remote",
            salary_text="18,000 NIS",
            description="Build backend APIs with Python.",
            required_skills=["Python"],
            years_experience="4+ years",
        ),
    )

    assert any(item.component == "experience_fit" and item.points_awarded == 8 for item in result.score_breakdown)
    assert any("slightly more experience" in concern for concern in result.concerns)


def test_score_job_skips_non_backend_role_without_backend_signals() -> None:
    result = score_job(
        make_profile(),
        Job(
            source="manual",
            source_url="https://example.com/jobs/designer",
            company="Example",
            title="Product Designer",
            location="Tel Aviv, Israel",
            remote_policy="Hybrid",
            salary_text="18,000 NIS",
            description="Own design systems and marketing assets.",
            required_skills=["Figma"],
            years_experience="2 years",
        ),
    )

    assert result.recommended_action == MatchRecommendation.SKIP
    assert any(item.component == "role_relevance" and item.points_awarded == 0 for item in result.score_breakdown)
