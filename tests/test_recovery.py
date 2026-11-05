import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from backend_scout.cli import _format_scout_run_summary, app
from backend_scout.config import TrackerName
from backend_scout.durable_runtime import ensure_today_scout
from backend_scout.models import ApplicationStatus
from backend_scout.notion import query_all_pages
from backend_scout.runtime_health import workflow_health
from backend_scout.scout_selection import select_reviewable_jobs
from backend_scout.submission import BrowserPreparationResult
from backend_scout.task_queue import (
    QueuedTaskKind,
    QueuedTaskStatus,
    claim_next_task,
    enqueue_task,
    list_tasks,
    mark_task_status,
    retry_task,
)


def test_scout_summary_distinguishes_tracked_pending_and_submitted():
    from collections import Counter

    summary = _format_scout_run_summary(
        tracker=TrackerName.PRODUCTION, collected=300, israel_relevant=200, shortlisted=0,
        created=0, already_tracked=13, failures=[], indeed_report=None,
        tracker_counts=Counter(digest_sent=23, submission_prepared=12, submitted=2, closed=3),
        filter_counts=Counter(excluded_title=20), source_counts={"Example": 300},
    )
    assert "Tracker backlog before submission: 35" in summary
    assert "Submitted status: 2" in summary
    assert "Tracked does not mean applied" in summary
    assert "not a market-wide no-jobs result" in summary
    assert "Already reviewed and skipped" not in summary


def test_old_high_scores_do_not_starve_new_jobs():
    jobs = [
        SimpleNamespace(job=SimpleNamespace(company=f"Company{index}", title="Backend"))
        for index in range(5)
    ]
    reviewed = {"properties": {"Status": {"status": {"name": "closed"}}}}
    selected, skipped = select_reviewable_jobs(
        jobs,
        lambda item: reviewed if item.job.company in {"Company0", "Company1", "Company2"} else None,
        2,
    )
    assert [item.job.company for item, _page in selected] == ["Company3", "Company4"]
    assert skipped == 3


def test_found_row_can_recover_failed_digest_delivery():
    page = {"properties": {"Status": {"status": {"name": "found"}}}}
    job = SimpleNamespace(job=SimpleNamespace(company="Company", title="Backend"))
    selected, _ = select_reviewable_jobs([job], lambda _: page, 1)
    assert selected == [(job, page)]


def test_two_sources_for_same_role_do_not_consume_two_digest_slots():
    first = SimpleNamespace(job=SimpleNamespace(company="Company", title="Backend"))
    second = SimpleNamespace(job=SimpleNamespace(company=" company ", title="BACKEND"))
    third = SimpleNamespace(job=SimpleNamespace(company="Other", title="Backend"))
    lookups = []
    selected, _ = select_reviewable_jobs([first, second, third], lambda job: lookups.append(job), 2)
    assert [job for job, _ in selected] == [first, third]
    assert lookups == [first, third]


def test_explicit_schedule_key_survives_completion(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    first = enqueue_task(
        QueuedTaskKind.SCOUT_TODAY,
        TrackerName.PRODUCTION,
        path=db,
        idempotency_key="schedule:scout:today",
    )
    mark_task_status(first.task_id, QueuedTaskStatus.DONE, path=db)
    again = enqueue_task(
        QueuedTaskKind.SCOUT_TODAY,
        TrackerName.PRODUCTION,
        path=db,
        idempotency_key="schedule:scout:today",
    )
    assert again.task_id == first.task_id


def test_watchdog_recovers_only_today_and_not_before_eight(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    ensure_today_scout(db, 1, datetime(2026, 10, 30, 5, tzinfo=UTC))
    assert not db.exists()
    for _ in range(3):
        ensure_today_scout(db, 1, datetime(2026, 10, 30, 7, tzinfo=UTC))
    assert len(list_tasks(db)) == 1
    assert list_tasks(db)[0].idempotency_key == "schedule:scout:2026-10-30"


def test_watchdog_recovers_exhausted_dns_failure_once_after_cooldown(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    now = datetime(2026, 11, 3, 9, tzinfo=UTC)
    ensure_today_scout(db, 1, now)
    original = list_tasks(db)[0]
    mark_task_status(original.task_id, QueuedTaskStatus.DEAD_LETTER,
                     "[Errno 8] nodename nor servname provided, or not known", path=db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE tasks SET updated_at=? WHERE task_id=?",
                           ((now - timedelta(minutes=20)).isoformat(), original.task_id))
    ensure_today_scout(db, 1, now)
    assert len(list_tasks(db)) == 1
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE tasks SET updated_at=? WHERE task_id=?",
                           ((now - timedelta(minutes=40)).isoformat(), original.task_id))
    for _ in range(3):
        ensure_today_scout(db, 1, now)
    tasks = list_tasks(db)
    assert len(tasks) == 2
    recovery = next(task for task in tasks if task.task_id != original.task_id)
    assert recovery.payload["recovery_of"] == original.task_id
    assert recovery.idempotency_key == "schedule:scout:2026-11-03:connectivity-recovery"
    mark_task_status(recovery.task_id, QueuedTaskStatus.DONE, path=db)
    ensure_today_scout(db, 1, now)
    assert len(list_tasks(db)) == 2


@pytest.mark.parametrize("status,error", [
    (QueuedTaskStatus.DEAD_LETTER, "Profile validation failed"),
    (QueuedTaskStatus.DONE, "nodename nor servname"),
    (QueuedTaskStatus.BLOCKED, "nodename nor servname"),
    (QueuedTaskStatus.CANCELLED, "nodename nor servname"),
])
def test_watchdog_does_not_replay_non_network_or_non_exhausted_tasks(tmp_path, status, error):
    db = tmp_path / "tasks.sqlite3"
    now = datetime(2026, 11, 3, 9, tzinfo=UTC)
    ensure_today_scout(db, 1, now)
    task = list_tasks(db)[0]
    mark_task_status(task.task_id, status, error, path=db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE tasks SET updated_at=? WHERE task_id=?",
                           ((now - timedelta(hours=1)).isoformat(), task.task_id))
    ensure_today_scout(db, 1, now)
    assert len(list_tasks(db)) == 1


def test_paused_mailbox_is_blocked_not_completed(monkeypatch, tmp_path):
    db = tmp_path / "tasks.sqlite3"
    enqueue_task(QueuedTaskKind.MAILBOX_SCAN, TrackerName.PRODUCTION, path=db)
    monkeypatch.setattr("backend_scout.cli.gmail_watch_once", lambda **_: False)
    result = CliRunner().invoke(app, ["tasks", "worker-once", "--queue-path", str(db)])
    assert result.exit_code == 0, result.output
    assert list_tasks(db)[0].status == QueuedTaskStatus.BLOCKED
    assert "completed" not in result.output
    assert claim_next_task(db) is None


def test_unavailable_sources_are_not_a_successful_scout(monkeypatch, tmp_path):
    db = tmp_path / "tasks.sqlite3"
    enqueue_task(QueuedTaskKind.SCOUT_TODAY, TrackerName.PRODUCTION, path=db)
    monkeypatch.setattr("backend_scout.cli.collect_run", lambda **_: False)
    result = CliRunner().invoke(app, ["tasks", "worker-once", "--queue-path", str(db)])
    assert result.exit_code == 0, result.output
    assert list_tasks(db)[0].status == QueuedTaskStatus.BLOCKED


def test_incomplete_portal_does_not_request_submit_or_succeed(monkeypatch, tmp_path):
    db = tmp_path / "tasks.sqlite3"
    enqueue_task(
        QueuedTaskKind.PORTAL_PREPARE, TrackerName.PRODUCTION, {"page_id": "page", "chat_id": 1}, db
    )
    monkeypatch.setattr(
        "backend_scout.cli.apply_prepare",
        lambda *_, **__: BrowserPreparationResult(
            state=ApplicationStatus.SUBMISSION_PREPARED.value,
            filled_fields=("email",),
            unresolved_required_fields=("cv_attachment",),
            message="CV upload missing",
        ),
    )
    messages = []
    monkeypatch.setattr("backend_scout.cli._send_task_telegram_message", lambda _task, message: messages.append(message))
    monkeypatch.setattr(
        "backend_scout.cli.apply_request_submit",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("must not submit")),
    )
    result = CliRunner().invoke(app, ["tasks", "worker-once", "--queue-path", str(db)])
    assert result.exit_code == 0, result.output
    assert list_tasks(db)[0].status == QueuedTaskStatus.BLOCKED
    assert "/submit_status" in messages[0]
    assert "Open in browser" in messages[0]
    assert "Opening the link does not submit" in messages[0]


def test_health_requires_recent_success_and_not_latest_blocked(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    for kind in (QueuedTaskKind.SCOUT_TODAY, QueuedTaskKind.MAILBOX_SCAN):
        task = enqueue_task(kind, TrackerName.PRODUCTION, path=db)
        mark_task_status(task.task_id, QueuedTaskStatus.DONE, path=db)
    assert workflow_health(db)["healthy"]
    task = enqueue_task(QueuedTaskKind.MAILBOX_SCAN, TrackerName.PRODUCTION, path=db)
    mark_task_status(task.task_id, QueuedTaskStatus.BLOCKED, path=db)
    assert not workflow_health(db)["healthy"]
    pending = enqueue_task(QueuedTaskKind.MAILBOX_SCAN, TrackerName.PRODUCTION, path=db)
    assert not workflow_health(db)["healthy"]
    mark_task_status(pending.task_id, QueuedTaskStatus.DONE, path=db)
    assert workflow_health(db)["healthy"]


def test_claiming_retry_does_not_clear_failed_health(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    for kind in (QueuedTaskKind.SCOUT_TODAY, QueuedTaskKind.MAILBOX_SCAN):
        task = enqueue_task(kind, TrackerName.PRODUCTION, path=db)
        mark_task_status(task.task_id, QueuedTaskStatus.DONE, path=db)
    pending = enqueue_task(QueuedTaskKind.MAILBOX_SCAN, TrackerName.PRODUCTION, path=db)
    claimed = claim_next_task(db)
    assert claimed.task_id == pending.task_id
    retry_task(pending.task_id, "API temporarily unavailable", db, delay_seconds=0)
    assert not workflow_health(db)["healthy"]
    claimed = claim_next_task(db)
    assert claimed.status == QueuedTaskStatus.RUNNING
    assert not workflow_health(db)["healthy"]
    mark_task_status(pending.task_id, QueuedTaskStatus.DONE, path=db)
    assert workflow_health(db)["healthy"]


def test_health_rejects_stale_scout_and_does_not_create_missing_database(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    assert not workflow_health(db)["healthy"]
    assert not db.exists()
    for kind in (QueuedTaskKind.SCOUT_TODAY, QueuedTaskKind.MAILBOX_SCAN):
        task = enqueue_task(kind, TrackerName.PRODUCTION, path=db)
        mark_task_status(task.task_id, QueuedTaskStatus.DONE, path=db)
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE tasks SET updated_at=? WHERE kind='scout_today'", (old,))
    assert not workflow_health(db)["components"]["scout_today"]["healthy"]


def test_mailbox_tracker_pagination_and_cursor_progress():
    responses = iter(
        [
            {
                "results": [{"id": str(i)} for i in range(100)],
                "has_more": True,
                "next_cursor": "next",
            },
            {"results": [{"id": "101"}], "has_more": False},
        ]
    )
    requests = []

    def query(source, payload):
        requests.append(dict(payload))
        return next(responses)

    pages = query_all_pages(SimpleNamespace(query_data_source=query), "source")
    assert len(pages) == 101
    assert requests[-1]["start_cursor"] == "next"
    stuck = SimpleNamespace(
        query_data_source=lambda *_: {
            "results": [],
            "has_more": True,
            "next_cursor": "same",
        }
    )
    with pytest.raises(ValueError, match="did not advance"):
        query_all_pages(stuck, "source")


@pytest.mark.parametrize("state", ["awaiting_human_verification", "submission_unknown"])
def test_unconfirmed_submission_stays_blocked(monkeypatch, tmp_path, state):
    db = tmp_path / "tasks.sqlite3"
    enqueue_task(
        QueuedTaskKind.PORTAL_SUBMIT, TrackerName.PRODUCTION, {"page_id": "page", "chat_id": 1}, db
    )
    monkeypatch.setattr(
        "backend_scout.cli.apply_resume",
        lambda *_, **__: BrowserPreparationResult(
            state=state,
            filled_fields=(),
            unresolved_required_fields=(),
            message="Needs review",
        ),
    )
    monkeypatch.setattr("backend_scout.cli._send_task_telegram_message", lambda *_: None)
    result = CliRunner().invoke(app, ["tasks", "worker-once", "--queue-path", str(db)])
    assert result.exit_code == 0, result.output
    assert list_tasks(db)[0].status == QueuedTaskStatus.BLOCKED
    assert claim_next_task(db) is None
