from pathlib import Path

import pytest
from pydantic import ValidationError

from backend_scout.candidate_profile import candidate_profile_warnings, load_candidate_profile
from backend_scout.models import CandidateProfile

VALID_PROFILE = {
    "name": "Test Candidate",
    "target_roles": ["Backend Engineer", "Backend Engineer", "  Software Engineer  "],
    "target_locations": ["Israel", "Remote"],
    "salary_floor_nis": 15000,
    "work_preferences": {"remote": True, "hybrid": True, "onsite": False},
    "seniority": {"min_years": 0, "max_years": 6},
    "core_skills": ["Python", "APIs"],
    "nice_to_have_skills": ["AI agents"],
    "proof_points": ["Built a backend service with a database-backed API."],
    "constraints": {
        "require_truthful_cv_only": True,
        "require_approval_before_submit": True,
    },
}


def test_candidate_profile_normalizes_duplicate_string_lists() -> None:
    profile = CandidateProfile.model_validate(VALID_PROFILE)

    assert profile.target_roles == ["Backend Engineer", "Software Engineer"]


def test_candidate_profile_rejects_impossible_seniority_range() -> None:
    data = VALID_PROFILE | {"seniority": {"min_years": 6, "max_years": 2}}

    with pytest.raises(ValidationError, match="max_years"):
        CandidateProfile.model_validate(data)


def test_candidate_profile_rejects_disabled_safety_constraints() -> None:
    data = VALID_PROFILE | {
        "constraints": {
            "require_truthful_cv_only": False,
            "require_approval_before_submit": True,
        }
    }

    with pytest.raises(ValidationError, match="require_truthful_cv_only"):
        CandidateProfile.model_validate(data)


def test_candidate_profile_rejects_negative_salary_floor() -> None:
    data = VALID_PROFILE | {"salary_floor_nis": -1}

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        CandidateProfile.model_validate(data)


def test_candidate_profile_requires_salary_floor() -> None:
    data = dict(VALID_PROFILE)
    data.pop("salary_floor_nis")

    with pytest.raises(ValidationError, match="salary_floor_nis"):
        CandidateProfile.model_validate(data)


def test_load_candidate_profile_reads_yaml_file(tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    profile_path.write_text(
        """
name: Test Candidate
target_roles:
  - Backend Engineer
target_locations:
  - Remote
salary_floor_nis: 15000
work_preferences:
  remote: true
  hybrid: true
  onsite: false
seniority:
  min_years: 0
  max_years: 6
core_skills:
  - Python
constraints:
  require_truthful_cv_only: true
  require_approval_before_submit: true
""".strip(),
        encoding="utf-8",
    )

    profile = load_candidate_profile(profile_path)

    assert profile.name == "Test Candidate"
    assert profile.salary_floor_nis == 15000
    assert "proof_points" in candidate_profile_warnings(profile)[0]
