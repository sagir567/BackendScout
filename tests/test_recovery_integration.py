from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from backend_scout import cli, durable_runtime
from backend_scout.config import TrackerName
from backend_scout.gmail import GmailMessageSummary
from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import build_job_page_properties
from backend_scout.runtime_health import workflow_health
from backend_scout.task_queue import (
    QueuedTaskKind,
    QueuedTaskStatus,
    enqueue_task,
    list_tasks,
    mark_task_status,
)


def test_same_role_from_two_sources_creates_only_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        Job(
            source=source,
            source_url=url,
            company="Example",
            title="Backend Engineer",
            description="Build APIs",
            location="Israel",
        )
        for source, url in [
            ("greenhouse", "https://example.com/job/1"),
            ("indeed_email", "https://indeed.example/job/2"),
        ]
    ]
    scored = [
        SimpleNamespace(job=job, result=SimpleNamespace(match_score=90, reason_summary="Relevant"))
        for job in jobs
    ]
    pages = []
    monkeypatch.setattr(
        cli,
        "load_target_companies",
        lambda _: SimpleNamespace(companies=[SimpleNamespace(enabled=True, name="Example")]),
    )
    monkeypatch.setattr(cli, "load_candidate_profile", lambda _: object())
    monkeypatch.setattr(
        cli, "load_scouting_preferences", lambda _: SimpleNamespace(maximum_digest_jobs=5)
    )
    monkeypatch.setattr(cli, "collect_public_jobs", lambda _: jobs)
    monkeypatch.setattr(cli, "score_jobs", lambda *_: scored)
    monkeypatch.setattr(cli, "_is_public_collection_shortlist", lambda *_: True)
    monkeypatch.setattr(cli, "_print_scored_jobs_table", lambda *_, **__: None)
    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: SimpleNamespace(
            notion_api_key="fake",
            notion_api_version="fake",
            notion_production_applications_data_source_id="source",
        ),
    )
    monkeypatch.setattr(cli, "validate_applications_data_source", lambda _: [])

    class FakeNotion:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def retrieve_data_source(self, _source):
            return {}

        def find_job_page(self, _source, job):
            return pages[0] if pages else None

        def create_job_page(self, _source, job):
            page = {"id": str(len(pages)), "properties": build_job_page_properties(job)}
            pages.append(page)
            return page

    monkeypatch.setattr(cli, "NotionClient", FakeNotion)
    assert cli.collect_run(
        write_notion=True, include_indeed_email=False, tracker=TrackerName.PRODUCTION
    )
    assert len(pages) == 1, f"Created {len(pages)} rows for one company/title"


def test_new_queued_scan_does_not_clear_blocked_health(tmp_path: Path) -> None:
    db = tmp_path / "tasks.sqlite3"
    for kind in (QueuedTaskKind.SCOUT_TODAY, QueuedTaskKind.MAILBOX_SCAN):
        task = enqueue_task(kind, TrackerName.PRODUCTION, path=db)
        mark_task_status(task.task_id, QueuedTaskStatus.DONE, path=db)
    blocked = enqueue_task(QueuedTaskKind.MAILBOX_SCAN, TrackerName.PRODUCTION, path=db)
    mark_task_status(
        blocked.task_id,
        QueuedTaskStatus.BLOCKED,
        last_error="Gmail reauthorization required",
        path=db,
    )
    assert not workflow_health(db)["healthy"]
    enqueue_task(
        QueuedTaskKind.MAILBOX_SCAN,
        TrackerName.PRODUCTION,
        path=db,
        idempotency_key="next-scheduled-scan",
    )
    report = workflow_health(db)
    assert not report["healthy"], report


@pytest.mark.parametrize("watchdog_first", [False, True])
def test_watchdog_and_scheduler_share_one_daily_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, watchdog_first: bool
) -> None:
    db = tmp_path / "tasks.sqlite3"
    when = datetime(2026, 10, 31, 6, tzinfo=UTC)
    monkeypatch.setattr(
        durable_runtime,
        "enqueue_scheduled_task",
        durable_runtime.enqueue_scheduled_task.__wrapped__,
    )

    def scheduled() -> None:
        durable_runtime.scheduled_production_scout.__wrapped__(
            when, {"database_path": str(db), "chat_id": 1}
        )

    def watchdog() -> None:
        durable_runtime.ensure_today_scout(db, 1, when)

    first, second = (watchdog, scheduled) if watchdog_first else (scheduled, watchdog)
    first()
    task = list_tasks(db)[0]
    mark_task_status(task.task_id, QueuedTaskStatus.DONE, path=db)
    second()
    assert len(list_tasks(db)) == 1


def test_mailbox_updates_application_on_second_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: SimpleNamespace(
            notion_api_key="fake",
            notion_api_version="fake",
            notion_production_applications_data_source_id="source",
        ),
    )
    monkeypatch.setattr(
        cli,
        "list_recent_messages",
        lambda *_: [
            GmailMessageSummary(
                message_id="message-1",
                thread_id="thread-1",
                from_header="jobs@lastcompany.example",
                subject="LastCompany interview",
                date_header="",
                snippet="Your application was selected for an interview.",
            )
        ],
    )
    pages = []
    for index in range(101):
        job = Job(
            source="test",
            source_url=f"https://example.com/job/{index}",
            company=f"Company{index}" if index < 100 else "LastCompany",
            title=f"Role{index}",
            description="Test application",
        )
        pages.append(
            {
                "id": str(index),
                "properties": build_job_page_properties(job, ApplicationStatus.SUBMITTED),
            }
        )
    requests = []
    updates = []
    records = []

    class FakeNotion:
        def __init__(self, *_: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def query_data_source(self, _source, payload):
            requests.append(dict(payload))
            if payload.get("start_cursor") == "next":
                return {"results": pages[100:], "has_more": False}
            return {"results": pages[:100], "has_more": True, "next_cursor": "next"}

        def update_application_status(self, page_id, status):
            updates.append((page_id, status))

        def append_submission_record(self, page_id, record):
            records.append((page_id, record))

    monkeypatch.setattr(cli, "NotionClient", FakeNotion)
    cli.gmail_watch_once(write_notion=True, state_path=tmp_path / "mailbox.json")
    assert len(requests) == 2
    assert updates == [("100", ApplicationStatus.INTERVIEW)]
    assert records[0][0] == "100"


def test_runtime_cli_prepares_before_activation_with_locked_release_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/fake/uv")

    def fake_deploy(_source, _runtime, prepare):
        assert not calls
        prepare(release)
        assert len(calls) == 2
        return runtime / "runtime-manifest.json"

    def fake_active(_runtime):
        assert len(calls) == 2
        return release

    monkeypatch.setattr(cli, "deploy_runtime_files", fake_deploy)
    monkeypatch.setattr(cli, "active_runtime_path", fake_active)
    cli.runtime_deploy(runtime_root=runtime, load=False)
    assert "--locked" in calls[0][0]
    assert calls[0][1]["env"]["UV_PROJECT_ENVIRONMENT"] == str(release / ".venv")
    assert calls[0][1]["cwd"] == release
    assert calls[1][0] == [str(release / ".venv" / "bin" / "backend-scout"), "--help"]
