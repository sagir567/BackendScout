from pathlib import Path

import pytest
from pydantic import ValidationError

from backend_scout.job_imports import (
    job_dedupe_key,
    job_with_match_result,
    load_manual_job_import,
    score_manual_job_import,
    unique_scored_jobs,
)
from backend_scout.models import CandidateProfile, ManualJobImport


def test_load_manual_job_import_reads_example_file() -> None:
    manual_import = load_manual_job_import(Path("examples/manual_job.example.yaml"))

    assert len(manual_import.jobs) == 1
    assert manual_import.jobs[0].company == "Example Cloud"
    assert manual_import.jobs[0].required_skills == [
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Docker",
    ]


def test_manual_job_import_rejects_empty_job_list() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ManualJobImport.model_validate({"jobs": []})


def test_manual_job_import_rejects_blank_required_job_fields() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        ManualJobImport.model_validate(
            {
                "jobs": [
                    {
                        "source": "manual",
                        "source_url": "https://example.com/jobs/backend",
                        "company": " ",
                        "title": "Backend Engineer",
                        "description": "Build APIs.",
                    }
                ]
            }
        )


def test_score_manual_job_import_returns_scored_jobs() -> None:
    manual_import = load_manual_job_import(Path("examples/manual_job.example.yaml"))
    profile = CandidateProfile.model_validate(
        {
            "name": "Test Candidate",
            "target_roles": ["Backend Engineer"],
            "target_locations": ["Israel", "Remote"],
            "salary_floor_nis": 15000,
            "work_preferences": {"remote": True, "hybrid": True, "onsite": False},
            "seniority": {"min_years": 0, "max_years": 3},
            "core_skills": ["Python", "FastAPI", "Docker"],
            "proof_points": ["Built Python and FastAPI backend services with Docker."],
            "constraints": {
                "require_truthful_cv_only": True,
                "require_approval_before_submit": True,
            },
        }
    )

    scored_jobs = score_manual_job_import(profile, manual_import)

    assert len(scored_jobs) == 1
    assert scored_jobs[0].result.match_score >= 0
    assert scored_jobs[0].result.reason_summary


def test_job_with_match_result_overrides_stale_yaml_score() -> None:
    manual_import = load_manual_job_import(Path("examples/manual_job.example.yaml"))
    profile = CandidateProfile.model_validate(
        {
            "name": "Test Candidate",
            "target_roles": ["Backend Engineer"],
            "target_locations": ["Israel", "Remote"],
            "salary_floor_nis": 15000,
            "work_preferences": {"remote": True, "hybrid": True, "onsite": False},
            "seniority": {"min_years": 0, "max_years": 3},
            "core_skills": ["Python", "FastAPI", "Docker"],
            "proof_points": ["Built Python and FastAPI backend services with Docker."],
            "constraints": {
                "require_truthful_cv_only": True,
                "require_approval_before_submit": True,
            },
        }
    )

    original_job = manual_import.jobs[0]
    assert original_job.match_score == 78

    scored_job = score_manual_job_import(profile, manual_import)[0]
    updated_job = job_with_match_result(original_job, scored_job)

    assert updated_job.match_score == scored_job.result.match_score
    assert updated_job.match_reason == scored_job.result.reason_summary


def test_job_dedupe_key_normalizes_job_identity_fields() -> None:
    manual_import = load_manual_job_import(Path("examples/manual_job.example.yaml"))
    job = manual_import.jobs[0].model_copy(
        update={
            "source": "  MANUAL ",
            "company": " Example   Cloud ",
            "title": " Backend   Engineer ",
            "source_url": " https://example.com/jobs/backend ",
        }
    )

    assert (
        job_dedupe_key(job)
        == "manual|example cloud|backend engineer|https://example.com/jobs/backend"
    )


def test_unique_scored_jobs_skips_duplicate_job_identities() -> None:
    manual_import = load_manual_job_import(Path("examples/manual_job.example.yaml"))
    profile = CandidateProfile.model_validate(
        {
            "name": "Test Candidate",
            "target_roles": ["Backend Engineer"],
            "target_locations": ["Israel", "Remote"],
            "salary_floor_nis": 15000,
            "work_preferences": {"remote": True, "hybrid": True, "onsite": False},
            "seniority": {"min_years": 0, "max_years": 3},
            "core_skills": ["Python", "FastAPI", "Docker"],
            "proof_points": ["Built Python and FastAPI backend services with Docker."],
            "constraints": {
                "require_truthful_cv_only": True,
                "require_approval_before_submit": True,
            },
        }
    )
    original_job = manual_import.jobs[0]
    duplicate_job = original_job.model_copy(update={"description": "Same job, refreshed note."})
    duplicated_import = ManualJobImport.model_validate({"jobs": [original_job, duplicate_job]})

    scored_jobs = score_manual_job_import(profile, duplicated_import)

    unique_jobs = unique_scored_jobs(scored_jobs)

    assert len(scored_jobs) == 2
    assert len(unique_jobs) == 1
