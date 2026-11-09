import json

from typer.testing import CliRunner

from backend_scout.cli import app
from backend_scout.gmail import GmailMessageSummary


def test_secret_email_joins_existing_scout_with_one_mailbox_read(monkeypatch, tmp_path):
    from test_cli import write_profile

    profile = tmp_path / "profile.yaml"
    write_profile(profile)
    preferences = tmp_path / "preferences.yaml"
    preferences.write_text(json.dumps({"include_secrethunter_email": True}))
    calls = []
    message = GmailMessageSummary(
        message_id="synthetic",
        thread_id=None,
        from_header="jobs@mail.secrethunter.io",
        subject="New jobs",
        date_header="",
        snippet="",
        body_html='<article data-title="Backend Engineer" data-company="Example">'
        "<h2>Backend Engineer</h2><p>Israel. Build Python backend APIs and Docker services.</p>"
        '<a href="https://secrethunter.io/search?selected=abc123">View job</a></article>',
    )
    monkeypatch.setattr("backend_scout.cli.collect_public_jobs", lambda target: [])
    monkeypatch.setattr(
        "backend_scout.cli.list_recent_messages",
        lambda query, limit: calls.append((query, limit)) or [message],
    )
    result = CliRunner().invoke(
        app,
        [
            "collect",
            "run",
            "--targets-path",
            "config/target_companies.example.yaml",
            "--profile-path",
            str(profile),
            "--preferences-path",
            str(preferences),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert "from:indeed" in calls[0][0] and "from:secrethunter.io" in calls[0][0]
    assert calls[0][1] == 50
    assert "SecretHunter email" in result.output
    assert "Preview only" in result.output
