"""Small local task queue for Telegram-first long-running work."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend_scout.config import TrackerName

DEFAULT_TASK_QUEUE_PATH = Path("data/tasks/tasks.json")


class QueuedTaskKind(str, Enum):
    SCOUT_TODAY = "scout_today"
    CV_DRAFT = "cv_draft"
    CONTACT_DISCOVERY = "contact_discovery"
    PORTAL_PREPARE = "portal_prepare"


class QueuedTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class QueuedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    kind: QueuedTaskKind
    tracker: TrackerName
    payload: dict[str, Any] = Field(default_factory=dict)
    status: QueuedTaskStatus = QueuedTaskStatus.QUEUED
    created_at: datetime
    updated_at: datetime
    last_error: str | None = None


class TaskQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[QueuedTask] = Field(default_factory=list)


def enqueue_task(
    kind: QueuedTaskKind,
    tracker: TrackerName,
    payload: dict[str, Any] | None = None,
    path: Path = DEFAULT_TASK_QUEUE_PATH,
) -> QueuedTask:
    queue = load_task_queue(path)
    now = datetime.now(UTC)
    task = QueuedTask(
        task_id=uuid4().hex[:12],
        kind=kind,
        tracker=tracker,
        payload=payload or {},
        created_at=now,
        updated_at=now,
    )
    queue.tasks.append(task)
    save_task_queue(queue, path)
    return task


def load_task_queue(path: Path = DEFAULT_TASK_QUEUE_PATH) -> TaskQueue:
    if not path.exists():
        return TaskQueue()
    return TaskQueue.model_validate_json(path.read_text(encoding="utf-8"))


def save_task_queue(queue: TaskQueue, path: Path = DEFAULT_TASK_QUEUE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(queue.model_dump_json(indent=2), encoding="utf-8")


def list_tasks(
    path: Path = DEFAULT_TASK_QUEUE_PATH,
    status: QueuedTaskStatus | None = None,
) -> list[QueuedTask]:
    tasks = load_task_queue(path).tasks
    if status is None:
        return tasks
    return [task for task in tasks if task.status == status]


def mark_task_status(
    task_id: str,
    status: QueuedTaskStatus,
    last_error: str | None = None,
    path: Path = DEFAULT_TASK_QUEUE_PATH,
) -> QueuedTask:
    queue = load_task_queue(path)
    for index, task in enumerate(queue.tasks):
        if task.task_id != task_id:
            continue
        updated = task.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(UTC),
                "last_error": last_error,
            }
        )
        queue.tasks[index] = updated
        save_task_queue(queue, path)
        return updated
    raise ValueError(f"Unknown queued task: {task_id}")
