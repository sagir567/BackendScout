from pathlib import Path

from typer.testing import CliRunner

from backend_scout.cli import app

runner = CliRunner()


def test_profile_check_accepts_valid_profile(tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    profile_path.write_text(
        """
name: Sagi
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

    result = runner.invoke(app, ["profile", "check", "--path", str(profile_path)])

    assert result.exit_code == 0
    assert "Candidate profile OK" in result.output


def test_jobs_validate_accepts_example_file() -> None:
    result = runner.invoke(app, ["jobs", "validate", "examples/manual_job.example.yaml"])

    assert result.exit_code == 0
    assert "Validated 1 job" in result.output


def test_jobs_import_is_preview_only_by_default() -> None:
    result = runner.invoke(app, ["jobs", "import", "examples/manual_job.example.yaml"])

    assert result.exit_code == 0
    assert "Preview only" in result.output


def test_jobs_score_outputs_score_and_action(tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    profile_path.write_text(
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
