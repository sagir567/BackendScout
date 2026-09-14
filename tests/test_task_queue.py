from pathlib import Path

import pytest

from backend_scout.config import TrackerName
from backend_scout.task_queue import (
    QueuedTaskKind,
    QueuedTaskStatus,
    enqueue_task,
    list_tasks,
    mark_task_status,
    next_queued_task,
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
