from pathlib import Path

import pytest

from backend_scout.config import TrackerName
from backend_scout.task_queue import (
    QueuedTaskKind,
    QueuedTaskStatus,
    enqueue_task,
    list_tasks,
    mark_task_status,
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
