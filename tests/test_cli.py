from pathlib import Path
from typing import Self

import pytest
from typer.testing import CliRunner

from backend_scout.cli import app
from backend_scout.config import TrackerName
from backend_scout.gmail import GmailMessageSummary
from backend_scout.models import ApplicationDigestItem, ApplicationStatus, Job
from backend_scout.notion import (
    APPLICATIONS_PROPERTY_NAMES,
    APPLICATIONS_PROPERTY_TYPES,
    build_job_page_properties,
)
from backend_scout.task_queue import QueuedTaskKind, QueuedTaskStatus, enqueue_task, list_tasks

runner = CliRunner()


def write_profile(path: Path) -> None:
    path.write_text(
        """
name: Test Candidate
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


def test_telegram_listener_help_is_available() -> None:
    result = runner.invoke(app, ["telegram", "listen", "--help"])

    assert result.exit_code == 0
    assert "Continuously process authorized Telegram workflow actions" in result.output


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


def test_collect_preferences_check_accepts_example_file() -> None:
    result = runner.invoke(
        app,
        ["collect", "preferences-check", "--preferences-path", "config/scouting_preferences.example.yaml"],
    )

    assert result.exit_code == 0
    assert "Scouting preferences OK" in result.output
    assert "Minimum match score: 55" in result.output


def test_system_launchd_install_and_status_use_agent_dir(tmp_path: Path) -> None:
    install_result = runner.invoke(
        app,
        ["system", "launchd", "install", "daily", "--agent-dir", str(tmp_path)],
    )

    assert install_result.exit_code == 0
    assert "Installed daily" in install_result.output
    assert (tmp_path / "com.backendscout.daily.plist").is_file()

    status_result = runner.invoke(app, ["system", "launchd", "status", "--agent-dir", str(tmp_path)])

    assert status_result.exit_code == 0
    assert "daily: installed" in status_result.output


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
                    "Application URL": {"type": "url"},
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
                    "Application URL": {"type": "url"},
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


def test_telegram_peek_updates_prints_pending_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTelegramClient:
        def __init__(self, bot_token: str) -> None:
            self.bot_token = bot_token

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get_updates(self, timeout: int = 0) -> list[dict[str, object]]:
            return [
                {
                    "update_id": 41,
                    "message": {
                        "from": {"id": 12345},
                        "text": "hello",
                    },
                }
            ]

    class FakeSettings:
        telegram_bot_token = "token"

    monkeypatch.setattr("backend_scout.cli.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("backend_scout.cli.Settings", FakeSettings)

    result = runner.invoke(app, ["telegram", "peek-updates"])

    assert result.exit_code == 0
    assert "user_id=12345" in result.output


def test_telegram_send_digest_queries_found_jobs_and_sends_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = []

    class FakeNotionClient:
        def __init__(self, api_key: str, api_version: str) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class FakeTelegramClient:
        def __init__(self, bot_token: str) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class FakeSettings:
        telegram_bot_token = "token"
        notion_api_key = "notion-token"
        notion_api_version = "2026-03-11"
        notion_applications_data_source_id = "data-source-123"

    job = ApplicationDigestItem(
        notion_page_id="page-123",
        company="Example Cloud",
        title="Backend Engineer",
        status=ApplicationStatus.FOUND,
        source="manual",
        source_url="https://example.com/jobs/backend",
        match_score=87,
    )

    monkeypatch.setattr("backend_scout.cli.NotionClient", FakeNotionClient)
    monkeypatch.setattr("backend_scout.cli.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("backend_scout.cli.Settings", FakeSettings)
    monkeypatch.setattr("backend_scout.cli.list_jobs_by_status", lambda *args, **kwargs: [job])
    monkeypatch.setattr(
        "backend_scout.cli.send_digest_messages",
        lambda telegram_client, notion_client, chat_id, jobs, tracker: sent.append((chat_id, jobs, tracker)) or [{}],
    )

    result = runner.invoke(app, ["telegram", "send-digest", "--chat-id", "12345"])

    assert result.exit_code == 0
    assert "Sent 1 digest message(s) to chat 12345." in result.output
    assert sent == [(12345, [job], TrackerName.TEST)]


def test_jobs_promote_creates_a_fresh_production_row_with_official_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile_path = tmp_path / "candidate_profile.yaml"
    write_profile(profile_path)
    created_jobs = []
    digests = []

    class FakeNotionClient:
        def __init__(self, api_key: str, api_version: str) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def retrieve_page(self, page_id: str) -> dict[str, object]:
            job = Job(
                company="ERGO NEXT",
                title="Junior Backend Software Engineer",
                source="indeed",
                source_url="https://il.indeed.com/viewjob?jk=example",
                description="Build backend services.",
                required_skills=["Python", ".NET"],
            )
            properties = build_job_page_properties(job, ApplicationStatus.DIGEST_SENT)
            return {"id": page_id, "parent": {"data_source_id": "test-id"}, "properties": properties}

        def retrieve_data_source(self, data_source_id: str) -> dict[str, object]:
            return {
                "properties": {
                    name: {"type": APPLICATIONS_PROPERTY_TYPES[key]}
                    for key, name in APPLICATIONS_PROPERTY_NAMES.items()
                }
            }

        def find_job_page(self, data_source_id: str, job) -> None:
            return None

        def create_job_page(self, data_source_id: str, job) -> dict[str, object]:
            created_jobs.append((data_source_id, job))
            return {
                "id": "production-page",
                "parent": {"data_source_id": data_source_id},
                "properties": build_job_page_properties(job),
            }

    class FakeTelegramClient:
        def __init__(self, bot_token: str) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class FakeSettings:
        notion_api_key = "notion-token"
        notion_api_version = "2026-03-11"
        notion_test_applications_data_source_id = "test-id"
        notion_production_applications_data_source_id = "production-id"
        telegram_bot_token = "telegram-token"
        telegram_default_chat_id = 12345
        telegram_allowed_user_id_set = frozenset({12345})

    monkeypatch.setattr("backend_scout.cli.NotionClient", FakeNotionClient)
    monkeypatch.setattr("backend_scout.cli.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("backend_scout.cli.Settings", FakeSettings)
    monkeypatch.setattr(
        "backend_scout.cli.send_digest_messages",
        lambda _telegram, _notion, chat_id, items, tracker: digests.append((chat_id, items, tracker)),
    )

    result = runner.invoke(
        app,
        [
            "jobs",
            "promote",
            "test-page",
            "--application-url",
            "https://careers.example/apply/5787617003",
            "--profile-path",
            str(profile_path),
        ],
    )

    assert result.exit_code == 0
    assert created_jobs[0][0] == "production-id"
    assert created_jobs[0][1].source_url == "https://il.indeed.com/viewjob?jk=example"
    assert created_jobs[0][1].application_url == "https://careers.example/apply/5787617003"
    assert digests[0][2] == TrackerName.PRODUCTION


def test_repos_validate_accepts_example_source_file() -> None:
    result = runner.invoke(app, ["repos", "validate", "--path", "config/repo_sources.example.yaml"])

    assert result.exit_code == 0
    assert "Repository sources OK" in result.output


def test_repos_scan_writes_local_proposal_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "main.cpp").write_text("int main() { return 0; }", encoding="utf-8")
    sources_path = tmp_path / "repo_sources.yaml"
    output_path = tmp_path / "report.json"
    sources_path.write_text(
        f"""
local_repositories:
  - name: CPP Practice
    path: {repo}
    enabled: true
github_repositories: []
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["repos", "scan", "--path", str(sources_path), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert "CPP Practice" in result.output
    assert output_path.is_file()


def test_tasks_worker_once_runs_queued_scout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "tasks.json"
    task = enqueue_task(
        QueuedTaskKind.SCOUT_TODAY,
        TrackerName.PRODUCTION,
        {"chat_id": 12345},
        queue_path,
    )
    calls = []
    monkeypatch.setattr("backend_scout.cli.collect_run", lambda **kwargs: calls.append(kwargs))

    result = runner.invoke(app, ["tasks", "worker-once", "--queue-path", str(queue_path)])

    assert result.exit_code == 0
    assert calls == [
        {
            "write_notion": True,
            "send_digest": True,
            "chat_id": 12345,
            "tracker": TrackerName.PRODUCTION,
        }
    ]
    assert list_tasks(queue_path)[0].status == QueuedTaskStatus.DONE
    assert task.task_id in result.output


def test_tasks_worker_once_runs_queued_cv_draft(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "tasks.json"
    enqueue_task(
        QueuedTaskKind.CV_DRAFT,
        TrackerName.PRODUCTION,
        {"page_id": "page-123", "chat_id": 12345},
        queue_path,
    )
    calls = []
    monkeypatch.setattr("backend_scout.cli.cv_draft", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = runner.invoke(app, ["tasks", "worker-once", "--queue-path", str(queue_path)])

    assert result.exit_code == 0
    assert calls == [
        (
            ("page-123",),
            {"chat_id": 12345, "tracker": TrackerName.PRODUCTION},
        )
    ]
    assert list_tasks(queue_path)[0].status == QueuedTaskStatus.DONE


def test_tasks_worker_once_runs_queued_portal_prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "tasks.json"
    enqueue_task(
        QueuedTaskKind.PORTAL_PREPARE,
        TrackerName.PRODUCTION,
        {"page_id": "page-123", "chat_id": 12345},
        queue_path,
    )
    calls = []
    monkeypatch.setattr("backend_scout.cli.apply_prepare", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = runner.invoke(app, ["tasks", "worker-once", "--queue-path", str(queue_path)])

    assert result.exit_code == 0
    assert calls == [(("page-123",), {"tracker": TrackerName.PRODUCTION})]
    assert list_tasks(queue_path)[0].status == QueuedTaskStatus.DONE


def test_gmail_watch_once_prints_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend_scout.cli.list_recent_messages",
        lambda query, max_results: [
            GmailMessageSummary(
                message_id="msg-1",
                thread_id="thread-1",
                from_header="jobs@example.com",
                subject="Coding challenge",
                date_header="Wed, 9 Sep 2026 08:00:00 +0300",
                snippet="Please complete this assessment.",
            )
        ],
    )

    result = runner.invoke(app, ["gmail", "watch-once"])

    assert result.exit_code == 0
    assert "Mailbox Application Updates" in result.output
    assert "assessment" in result.output
