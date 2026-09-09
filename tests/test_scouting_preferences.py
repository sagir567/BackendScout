from pathlib import Path

from backend_scout.candidate_profile import load_candidate_profile
from backend_scout.matcher import ScoredJob, score_job
from backend_scout.models import Job
from backend_scout.scouting_preferences import (
    ScoutingPreferences,
    load_scouting_preferences,
    public_collection_digest_candidate,
)


def _profile_path(tmp_path: Path) -> Path:
    path = tmp_path / "candidate_profile.yaml"
    path.write_text(
        """
name: Test Candidate
target_roles:
  - Backend Engineer
  - Software Engineer
target_locations:
  - Israel
salary_floor_nis: 15000
work_preferences:
  remote: true
  hybrid: true
  onsite: false
seniority:
  min_years: 0
  max_years: 3
core_skills:
  - Python
  - Docker
  - APIs
proof_points:
  - Built Python backend APIs with Docker.
constraints:
  require_truthful_cv_only: true
  require_approval_before_submit: true
""".strip(),
        encoding="utf-8",
    )
    return path


def _scored_job(tmp_path: Path, title: str) -> ScoredJob:
    profile = load_candidate_profile(_profile_path(tmp_path))
    job = Job(
        source="test",
        source_url="https://example.com/job",
        company="Example",
        title=title,
        location="Tel Aviv, Israel",
        description="Build Python backend APIs with Docker.",
        required_skills=["Python", "Docker", "APIs"],
        years_experience="2+ years",
    )
    return ScoredJob(job=job, result=score_job(profile, job))


def test_scouting_preferences_validate_example_file() -> None:
    preferences = load_scouting_preferences(Path("config/scouting_preferences.example.yaml"))

    assert "Backend" in preferences.preferred_title_keywords
    assert preferences.minimum_match_score == 55


def test_public_collection_digest_candidate_accepts_backend_role(tmp_path: Path) -> None:
    scored_job = _scored_job(tmp_path, "Backend Engineer")

    assert public_collection_digest_candidate(scored_job, ScoutingPreferences())


def test_public_collection_digest_candidate_rejects_excluded_title(tmp_path: Path) -> None:
    scored_job = _scored_job(tmp_path, "Senior Product Manager")
    preferences = ScoutingPreferences(excluded_title_keywords=["Product Manager"])

    assert not public_collection_digest_candidate(scored_job, preferences)


def test_public_collection_digest_candidate_honors_minimum_score(tmp_path: Path) -> None:
    scored_job = _scored_job(tmp_path, "Backend Engineer")
    preferences = ScoutingPreferences(minimum_match_score=99)

    assert not public_collection_digest_candidate(scored_job, preferences)
