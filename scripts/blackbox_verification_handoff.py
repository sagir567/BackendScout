"""Run the headed-browser human-verification handoff against a local fixture."""

from pathlib import Path
from tempfile import TemporaryDirectory

from backend_scout.career_evidence import load_career_evidence
from backend_scout.submission import prepare_visible_submission


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    fixture_url = (project_root / "tests/fixtures/verification_handoff.html").as_uri()
    attachment = project_root / "README.md"
    evidence = load_career_evidence(project_root / "config/career_evidence.yaml")

    with TemporaryDirectory(prefix="backendscout-verification-") as profile_dir:
        result = prepare_visible_submission(
            fixture_url,
            Path(profile_dir),
            evidence,
            attachment,
            wait_for_human_seconds=5,
        )

    expected = {"first_name", "last_name", "email", "cv_attachment"}
    missing = expected.difference(result.filled_fields)
    if result.state != "submission_prepared" or missing:
        raise SystemExit(
            f"Black-box handoff failed: state={result.state}, missing={sorted(missing)}"
        )
    print("Black-box handoff passed: the same browser session resumed after verification.")


if __name__ == "__main__":
    main()
