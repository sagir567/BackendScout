from pathlib import Path
from typing import Self

import pytest
from typer.testing import CliRunner

from backend_scout.cli import app

runner = CliRunner()


def write_profile(path: Path) -> None:
    path.write_text(
        """
name: Sagi
target_roles:
  - Backend Engineer
target_locations:
  - Israel
  - Remote
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
  - FastAPI
  - Docker
proof_points:
  - Built Python and FastAPI backend services deployed with Docker.
constraints:
  require_truthful_cv_only: true
  require_approval_before_submit: true
""".strip(),
        encoding="utf-8",
    )


def test_profile_check_accepts_valid_profile(tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    write_profile(profile_path)

    result = runner.invoke(app, ["profile", "check", "--path", str(profile_path)])

    assert result.exit_code == 0
    assert "Candidate profile OK" in result.output


def test_jobs_validate_accepts_example_file() -> None:
    result = runner.invoke(app, ["jobs", "validate", "examples/manual_job.example.yaml"])

    assert result.exit_code == 0
    assert "Validated 1 job" in result.output


def test_jobs_import_is_preview_only_by_default() -> None:
    profile_path = Path.cwd() / ".tmp-test-profile.yaml"
    write_profile(profile_path)

    result = runner.invoke(
        app,
        [
            "jobs",
            "import",
            "examples/manual_job.example.yaml",
            "--profile-path",
            str(profile_path),
        ],
    )

    try:
        assert result.exit_code == 0
        assert "Manual Job Import Shortlist" in result.output
        assert "Preview only" in result.output
    finally:
        profile_path.unlink(missing_ok=True)


def test_jobs_score_outputs_score_and_action(tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    write_profile(profile_path)

    result = runner.invoke(
        app,
        [
            "jobs",
            "score",
            "examples/manual_job.example.yaml",
            "--profile-path",
            str(profile_path),
        ],
    )

    assert result.exit_code == 0
    assert "Job Match Summary" in result.output
    assert "Example Cloud" in result.output
    assert "Action:" in result.output
    assert "Breakdown:" in result.output


def test_jobs_import_write_notion_uses_fresh_scoring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    write_profile(profile_path)

    synced_jobs = []

    class FakeNotionClient:
        def __init__(self, api_key: str, api_version: str) -> None:
            self.api_key = api_key
            self.api_version = api_version

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def retrieve_data_source(self, data_source_id: str) -> dict[str, object]:
            return {
                "properties": {
                    "Role": {"type": "title"},
                    "Company": {"type": "rich_text"},
                    "Status": {"type": "status"},
                    "Source": {"type": "rich_text"},
                    "Source URL": {"type": "url"},
                    "Location": {"type": "rich_text"},
                    "Remote Policy": {"type": "rich_text"},
                    "Employment Type": {"type": "rich_text"},
                    "Salary": {"type": "rich_text"},
                    "Match Score": {"type": "number"},
                    "Required Skills": {"type": "multi_select"},
                    "Years Experience": {"type": "rich_text"},
                    "Match Reason": {"type": "rich_text"},
                    "Description": {"type": "rich_text"},
                    "Discovered At": {"type": "date"},
                }
            }

        def find_job_page(self, data_source_id: str, job) -> None:
            return None

        def create_job_page(self, data_source_id: str, job, status=None) -> dict[str, object]:
            synced_jobs.append(("created", job))
            return {"id": "page_123"}

        def update_job_page(self, page_id: str, job) -> dict[str, object]:
            synced_jobs.append(("updated", job))
            return {"id": page_id}

    class FakeSettings:
        notion_api_key = "token"
        notion_api_version = "2026-03-11"
        notion_applications_data_source_id = "data-source-123"

    monkeypatch.setattr("backend_scout.cli.NotionClient", FakeNotionClient)
    monkeypatch.setattr("backend_scout.cli.Settings", FakeSettings)

    result = runner.invoke(
        app,
        [
            "jobs",
            "import",
            "examples/manual_job.example.yaml",
            "--profile-path",
            str(profile_path),
            "--write-notion",
        ],
    )

    assert result.exit_code == 0
    assert "Synced 1 job(s) to Notion." in result.output
    assert "Created: 1, Updated: 0." in result.output
    assert len(synced_jobs) == 1
    assert synced_jobs[0][0] == "created"
    assert synced_jobs[0][1].match_score != 78
    assert synced_jobs[0][1].match_reason.startswith("Recommendation:")


def test_jobs_import_dedupes_input_and_updates_existing_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    write_profile(profile_path)
    jobs_path = tmp_path / "jobs.yaml"
    jobs_path.write_text(
        """
jobs:
  - source: manual
    source_url: https://example.com/jobs/backend
    company: Example Cloud
    title: Backend Engineer
    description: First snapshot.
  - source: manual
    source_url: https://example.com/jobs/backend
    company: Example Cloud
    title: Backend Engineer
    description: Duplicate snapshot.
""".strip(),
        encoding="utf-8",
    )

    synced_jobs = []

    class FakeNotionClient:
        def __init__(self, api_key: str, api_version: str) -> None:
            self.api_key = api_key
            self.api_version = api_version

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def retrieve_data_source(self, data_source_id: str) -> dict[str, object]:
            return {
                "properties": {
                    "Role": {"type": "title"},
                    "Company": {"type": "rich_text"},
                    "Status": {"type": "status"},
                    "Source": {"type": "rich_text"},
                    "Source URL": {"type": "url"},
                    "Location": {"type": "rich_text"},
                    "Remote Policy": {"type": "rich_text"},
                    "Employment Type": {"type": "rich_text"},
                    "Salary": {"type": "rich_text"},
                    "Match Score": {"type": "number"},
                    "Required Skills": {"type": "multi_select"},
                    "Years Experience": {"type": "rich_text"},
                    "Match Reason": {"type": "rich_text"},
                    "Description": {"type": "rich_text"},
                    "Discovered At": {"type": "date"},
                }
            }

        def find_job_page(self, data_source_id: str, job) -> dict[str, str]:
            return {"id": "page_existing"}

        def create_job_page(self, data_source_id: str, job, status=None) -> dict[str, object]:
            synced_jobs.append(("created", job))
            return {"id": "page_new"}

        def update_job_page(self, page_id: str, job) -> dict[str, object]:
            synced_jobs.append(("updated", page_id, job))
            return {"id": page_id}

    class FakeSettings:
        notion_api_key = "token"
        notion_api_version = "2026-03-11"
        notion_applications_data_source_id = "data-source-123"

    monkeypatch.setattr("backend_scout.cli.NotionClient", FakeNotionClient)
    monkeypatch.setattr("backend_scout.cli.Settings", FakeSettings)

    result = runner.invoke(
        app,
        [
            "jobs",
            "import",
            str(jobs_path),
            "--profile-path",
            str(profile_path),
            "--write-notion",
        ],
    )

    assert result.exit_code == 0
    assert "Synced 1 job(s) to Notion." in result.output
    assert "Created: 0, Updated: 1." in result.output
    assert len(synced_jobs) == 1
    assert synced_jobs[0][0] == "updated"
