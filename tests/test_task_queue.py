from pathlib import Path

import pytest

from backend_scout.config import TrackerName
from backend_scout.task_queue import (
    BROWSER_TASK_KINDS,
    GENERAL_TASK_KINDS,
    QueuedTaskKind,
    QueuedTaskStatus,
    claim_next_task,
    enqueue_task,
    list_tasks,
    mark_task_status,
    next_queued_task,
    retry_task,
)


def test_enqueue_task_persists_local_queue(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"

    task = enqueue_task(
        QueuedTaskKind.SCOUT_TODAY,
        TrackerName.PRODUCTION,
        {"chat_id": 12345},
        path,
    )

    tasks = list_tasks(path)
    assert len(tasks) == 1
    assert tasks[0].task_id == task.task_id
    assert tasks[0].tracker == TrackerName.PRODUCTION
    assert tasks[0].payload == {"chat_id": 12345}


def test_mark_task_status_updates_one_task(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    task = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.TEST, path=path)

    updated = mark_task_status(task.task_id, QueuedTaskStatus.FAILED, "captcha", path)

    assert updated.status == QueuedTaskStatus.FAILED
    assert updated.last_error == "captcha"
    assert list_tasks(path, QueuedTaskStatus.FAILED)[0].task_id == task.task_id


def test_mark_task_status_rejects_unknown_task(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown queued task"):
        mark_task_status("missing", QueuedTaskStatus.DONE, path=tmp_path / "tasks.json")


def test_enqueue_task_deduplicates_matching_active_work(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    payload = {"page_id": "page-123", "chat_id": 12345}

    first = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.PRODUCTION, payload, path)
    duplicate = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.PRODUCTION, payload, path)

    assert duplicate.task_id == first.task_id
    assert len(list_tasks(path)) == 1


def test_completed_work_can_be_enqueued_again(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    payload = {"page_id": "page-123", "chat_id": 12345}
    first = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.PRODUCTION, payload, path)
    mark_task_status(first.task_id, QueuedTaskStatus.DONE, path=path)

    repeated = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.PRODUCTION, payload, path)

    assert repeated.task_id != first.task_id
    assert len(list_tasks(path)) == 2


def test_next_queued_task_prioritizes_final_submit(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    enqueue_task(
        QueuedTaskKind.PORTAL_PREPARE,
        TrackerName.PRODUCTION,
        {"page_id": "prepare-page"},
        path,
    )
    submit = enqueue_task(
        QueuedTaskKind.PORTAL_SUBMIT,
        TrackerName.PRODUCTION,
        {"page_id": "submit-page"},
        path,
    )

    selected = next_queued_task(path)

    assert selected is not None
    assert selected.task_id == submit.task_id


def test_sqlite_claim_is_atomic_and_records_attempt(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    task = enqueue_task(QueuedTaskKind.CV_DRAFT, TrackerName.PRODUCTION, path=path)

    claimed = claim_next_task(path, lease_seconds=60)

    assert claimed is not None
    assert claimed.task_id == task.task_id
    assert claimed.status == QueuedTaskStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.lease_until is not None
    assert claim_next_task(path) is None


def test_sqlite_retry_moves_exhausted_task_to_dead_letter(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    task = enqueue_task(
        QueuedTaskKind.PORTAL_PREPARE,
        TrackerName.PRODUCTION,
        path=path,
        max_attempts=1,
    )
    claim_next_task(path)

    failed = retry_task(task.task_id, "frame detached", path, delay_seconds=0)

    assert failed.status == QueuedTaskStatus.DEAD_LETTER
    assert failed.last_error == "frame detached"


def test_sqlite_deduplicates_active_work(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    payload = {"page_id": "page-123"}
    first = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.TEST, payload, path)
    duplicate = enqueue_task(QueuedTaskKind.PORTAL_PREPARE, TrackerName.TEST, payload, path)

    assert duplicate.task_id == first.task_id
    assert len(list_tasks(path)) == 1


def test_sqlite_worker_lanes_claim_only_their_own_tasks(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    general = enqueue_task(QueuedTaskKind.SCOUT_TODAY, TrackerName.PRODUCTION, path=path)
    browser = enqueue_task(QueuedTaskKind.PORTAL_SUBMIT, TrackerName.PRODUCTION, path=path)

    claimed_general = claim_next_task(path, allowed_kinds=GENERAL_TASK_KINDS)
    claimed_browser = claim_next_task(path, allowed_kinds=BROWSER_TASK_KINDS)

    assert claimed_general is not None and claimed_general.task_id == general.task_id
    assert claimed_browser is not None and claimed_browser.task_id == browser.task_id
