from pathlib import Path

import pytest

from backend_scout.candidate_profile import load_candidate_profile
from backend_scout.matcher import ScoredJob, score_job
from backend_scout.models import Job
from backend_scout.scouting_preferences import (
    ScoutingPreferences,
    _contains_any,
    _normalize,
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


@pytest.mark.parametrize(
    "title",
    ["Backend Engineer - Billing", "Mobile Backend Engineer", "Backend Engineer - Leaderboards"],
)
def test_exclusions_do_not_match_inside_title_words(tmp_path: Path, title: str) -> None:
    scored_job = _scored_job(tmp_path, title)
    preferences = ScoutingPreferences(excluded_title_keywords=["BI", "Lead"])

    assert public_collection_digest_candidate(scored_job, preferences)


@pytest.mark.parametrize(
    ("title", "keyword"),
    [
        ("Backend Engineer (BI)", "BI"),
        ("Backend/BI Developer", "BI"),
        ("Senior Backend Engineer", "Senior"),
        ("Backend Team Lead", "Lead"),
        ("Backend Team-Lead", "Team Lead"),
        ("Backend Engineering Manager", "Manager"),
    ],
)
def test_explicit_exclusions_still_override_preferred_titles(
    tmp_path: Path, title: str, keyword: str
) -> None:
    scored_job = _scored_job(tmp_path, title)

    assert public_collection_digest_candidate(scored_job, ScoutingPreferences())
    assert not public_collection_digest_candidate(
        scored_job, ScoutingPreferences(excluded_title_keywords=[keyword])
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Backend Engineer", True),
        ("Backend Engineer - \u05de\u05e0\u05d4\u05dc \u05e6\u05d5\u05d5\u05ea", False),
    ],
    ids=["unrelated-hebrew-exclusion", "explicit-hebrew-exclusion"],
)
def test_hebrew_exclusion_only_matches_its_title(
    tmp_path: Path, title: str, expected: bool
) -> None:
    scored_job = _scored_job(tmp_path, title)
    preferences = ScoutingPreferences(
        excluded_title_keywords=["\u05de\u05e0\u05d4\u05dc \u05e6\u05d5\u05d5\u05ea"]
    )

    assert public_collection_digest_candidate(scored_job, preferences) is expected


@pytest.mark.parametrize(
    ("title", "keyword", "expected"),
    [
        ("Backend Engineer", "Backend", True),
        ("Back-end Engineer", "Backend", True),
        ("Backend Engineer", "Back-end", True),
        ("Software-Engineer / Backend", "Software Engineer", True),
        ("Software Platform Engineer", "Software Engineer", False),
        ("Software Engineers", "Software Engineer", False),
        ("Backend/.NET Engineer", ".NET", True),
        ("Internet Backend Engineer", ".NET", False),
        ("Backend (C++) Engineer", "C++", True),
        ("Backend C Engineer", "C++", False),
        ("Backend C++ Engineer", "C", False),
        ("Backend C# Engineer", "C", False),
        ("Backend Engineer", "...", False),
        ("Backend Engineer", " ", False),
    ],
)
def test_title_keywords_match_whole_tokens_and_phrases(
    title: str, keyword: str, expected: bool
) -> None:
    assert _contains_any(_normalize(title), [keyword]) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("\u05de\u05e4\u05ea\u05d7 \u05ea\u05d5\u05db\u05e0\u05d4 / Python", True),
        ("\u05de\u05e4\u05ea\u05d7\u05d9 \u05ea\u05d5\u05db\u05e0\u05d4", False),
        ("Backend Engineer", False),
    ],
    ids=["hebrew-phrase", "hebrew-whole-token", "unrelated-title"],
)
def test_hebrew_keywords_preserve_words_and_phrase_boundaries(title: str, expected: bool) -> None:
    assert _contains_any(
        _normalize(title), ["\u05de\u05e4\u05ea\u05d7 \u05ea\u05d5\u05db\u05e0\u05d4"]
    ) is expected
