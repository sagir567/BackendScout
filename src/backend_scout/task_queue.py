"""Durable local task queue with legacy JSON compatibility."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend_scout.config import TrackerName

DEFAULT_TASK_QUEUE_PATH = Path("data/runtime/backendscout.sqlite3")
LEGACY_TASK_QUEUE_PATH = Path("data/tasks/tasks.json")


class QueuedTaskKind(str, Enum):
    SCOUT_TODAY = "scout_today"
    MAILBOX_SCAN = "mailbox_scan"
    AGENT_CHAT = "agent_chat"
    CV_DRAFT = "cv_draft"
    CONTACT_DISCOVERY = "contact_discovery"
    PORTAL_PREPARE = "portal_prepare"
    PORTAL_SUBMIT = "portal_submit"


class QueuedTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    DONE = "done"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


BROWSER_TASK_KINDS = frozenset(
    {QueuedTaskKind.PORTAL_PREPARE, QueuedTaskKind.PORTAL_SUBMIT}
)
GENERAL_TASK_KINDS = frozenset(set(QueuedTaskKind) - BROWSER_TASK_KINDS)


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
    attempts: int = 0
    max_attempts: int = 3
    available_at: datetime | None = None
    lease_until: datetime | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None


class TaskQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[QueuedTask] = Field(default_factory=list)


def enqueue_task(
    kind: QueuedTaskKind,
    tracker: TrackerName,
    payload: dict[str, Any] | None = None,
    path: Path = DEFAULT_TASK_QUEUE_PATH,
    *,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> QueuedTask:
    if _is_json_path(path):
        return _json_enqueue_task(kind, tracker, payload, path)
    requested_payload = payload or {}
    key = idempotency_key or _task_key(kind, tracker, requested_payload)
    now = datetime.now(UTC)
    with _transaction(path) as connection:
        existing = connection.execute(
            """
            SELECT * FROM tasks
            WHERE idempotency_key = ? AND status IN ('queued', 'running', 'retrying')
            ORDER BY created_at DESC LIMIT 1
            """,
            (key,),
        ).fetchone()
        if existing is not None:
            return _row_to_task(existing)
        task = QueuedTask(
            task_id=uuid4().hex[:12],
            kind=kind,
            tracker=tracker,
            payload=requested_payload,
            created_at=now,
            updated_at=now,
            available_at=now,
            max_attempts=max_attempts,
            idempotency_key=key,
            correlation_id=correlation_id or uuid4().hex[:16],
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, kind, tracker, payload_json, status, created_at, updated_at,
                last_error, attempts, max_attempts, available_at, lease_until,
                correlation_id, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _task_values(task),
        )
        _append_event(connection, task.task_id, "enqueued", {"kind": kind.value})
        return task


def claim_next_task(
    path: Path = DEFAULT_TASK_QUEUE_PATH,
    *,
    lease_seconds: int = 900,
    allowed_kinds: frozenset[QueuedTaskKind] | None = None,
) -> QueuedTask | None:
    """Atomically claim the highest-priority available task."""
    if _is_json_path(path):
        task = next_queued_task(path, allowed_kinds=allowed_kinds)
        if task is None:
            return None
        return mark_task_status(task.task_id, QueuedTaskStatus.RUNNING, path=path)
    now = datetime.now(UTC)
    with _transaction(path) as connection:
        _recover_expired(connection, now)
        kind_filter = ""
        parameters: list[str] = [_dt(now)]
        if allowed_kinds is not None:
            if not allowed_kinds:
                return None
            placeholders = ", ".join("?" for _ in allowed_kinds)
            kind_filter = f" AND kind IN ({placeholders})"
            parameters.extend(kind.value for kind in sorted(allowed_kinds, key=lambda item: item.value))
        row = connection.execute(
            f"""
            SELECT * FROM tasks
            WHERE status IN ('queued', 'retrying')
              AND (available_at IS NULL OR available_at <= ?)
              {kind_filter}
            ORDER BY
              CASE kind
                WHEN 'portal_submit' THEN 0
                WHEN 'cv_draft' THEN 10
                WHEN 'portal_prepare' THEN 20
                WHEN 'contact_discovery' THEN 30
                WHEN 'agent_chat' THEN 35
                WHEN 'scout_today' THEN 40
                WHEN 'mailbox_scan' THEN 50
                ELSE 100
              END,
              created_at
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        if row is None:
            return None
        lease_until = now + timedelta(seconds=lease_seconds)
        connection.execute(
            """
            UPDATE tasks
            SET status = 'running', attempts = attempts + 1, updated_at = ?, lease_until = ?
            WHERE task_id = ?
            """,
            (_dt(now), _dt(lease_until), row["task_id"]),
        )
        _append_event(connection, row["task_id"], "claimed", {"lease_until": _dt(lease_until)})
        claimed = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (row["task_id"],)
        ).fetchone()
        return _row_to_task(claimed)


def next_queued_task(
    path: Path = DEFAULT_TASK_QUEUE_PATH,
    *,
    allowed_kinds: frozenset[QueuedTaskKind] | None = None,
) -> QueuedTask | None:
    if _is_json_path(path):
        queued = list_tasks(path, QueuedTaskStatus.QUEUED)
    else:
        now = _dt(datetime.now(UTC))
        with _connection(path) as connection:
            rows = connection.execute(
                """SELECT * FROM tasks WHERE status IN ('queued', 'retrying')
                AND (available_at IS NULL OR available_at <= ?)""",
                (now,),
            ).fetchall()
        queued = [_row_to_task(row) for row in rows]
    if allowed_kinds is not None:
        queued = [task for task in queued if task.kind in allowed_kinds]
    if not queued:
        return None
    priorities = {
        QueuedTaskKind.PORTAL_SUBMIT: 0,
        QueuedTaskKind.CV_DRAFT: 10,
        QueuedTaskKind.PORTAL_PREPARE: 20,
        QueuedTaskKind.CONTACT_DISCOVERY: 30,
        QueuedTaskKind.AGENT_CHAT: 35,
        QueuedTaskKind.SCOUT_TODAY: 40,
        QueuedTaskKind.MAILBOX_SCAN: 50,
    }
    return min(queued, key=lambda task: (priorities[task.kind], task.created_at))


def list_tasks(
    path: Path = DEFAULT_TASK_QUEUE_PATH,
    status: QueuedTaskStatus | None = None,
) -> list[QueuedTask]:
    if _is_json_path(path):
        tasks = load_task_queue(path).tasks
        return tasks if status is None else [task for task in tasks if task.status == status]
    with _connection(path) as connection:
        if status is None:
            rows = connection.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at", (status.value,)
            ).fetchall()
    return [_row_to_task(row) for row in rows]


def mark_task_status(
    task_id: str,
    status: QueuedTaskStatus,
    last_error: str | None = None,
    path: Path = DEFAULT_TASK_QUEUE_PATH,
) -> QueuedTask:
    if _is_json_path(path):
        return _json_mark_task_status(task_id, status, last_error, path)
    now = datetime.now(UTC)
    with _transaction(path) as connection:
        current = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if current is None:
            raise ValueError(f"Unknown queued task: {task_id}")
        connection.execute(
            """UPDATE tasks SET status = ?, updated_at = ?, last_error = ?, lease_until = NULL
            WHERE task_id = ?""",
            (status.value, _dt(now), last_error, task_id),
        )
        _append_event(connection, task_id, status.value, {"error": last_error})
        updated = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return _row_to_task(updated)


def retry_task(
    task_id: str,
    error: str,
    path: Path = DEFAULT_TASK_QUEUE_PATH,
    *,
    delay_seconds: int = 30,
) -> QueuedTask:
    """Retry with backoff or move an exhausted task to the dead letter state."""
    if _is_json_path(path):
        return mark_task_status(task_id, QueuedTaskStatus.FAILED, error, path)
    now = datetime.now(UTC)
    with _transaction(path) as connection:
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown queued task: {task_id}")
        status = (
            QueuedTaskStatus.DEAD_LETTER
            if row["attempts"] >= row["max_attempts"]
            else QueuedTaskStatus.RETRYING
        )
        available_at = now + timedelta(seconds=delay_seconds * max(1, row["attempts"]))
        connection.execute(
            """UPDATE tasks SET status = ?, updated_at = ?, last_error = ?,
            available_at = ?, lease_until = NULL WHERE task_id = ?""",
            (status.value, _dt(now), error, _dt(available_at), task_id),
        )
        _append_event(connection, task_id, status.value, {"error": error})
        updated = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _row_to_task(updated)


def migrate_legacy_queue(
    source: Path = LEGACY_TASK_QUEUE_PATH,
    destination: Path = DEFAULT_TASK_QUEUE_PATH,
) -> int:
    """Import legacy JSON tasks once without changing their historical state."""
    if not source.exists():
        return 0
    queue = load_task_queue(source)
    imported = 0
    with _transaction(destination) as connection:
        for task in queue.tasks:
            if connection.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (task.task_id,)
            ).fetchone():
                continue
            normalized = task.model_copy(
                update={
                    "idempotency_key": task.idempotency_key
                    or _task_key(task.kind, task.tracker, task.payload),
                    "correlation_id": task.correlation_id or uuid4().hex[:16],
                    "available_at": task.available_at or task.created_at,
                }
            )
            connection.execute(
                """INSERT INTO tasks (
                task_id, kind, tracker, payload_json, status, created_at, updated_at,
                last_error, attempts, max_attempts, available_at, lease_until,
                correlation_id, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _task_values(normalized),
            )
            _append_event(connection, task.task_id, "legacy_imported", {})
            imported += 1
    return imported


def load_task_queue(path: Path = LEGACY_TASK_QUEUE_PATH) -> TaskQueue:
    if not path.exists():
        return TaskQueue()
    return TaskQueue.model_validate_json(path.read_text(encoding="utf-8"))


def save_task_queue(queue: TaskQueue, path: Path = LEGACY_TASK_QUEUE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(queue.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _json_enqueue_task(
    kind: QueuedTaskKind,
    tracker: TrackerName,
    payload: dict[str, Any] | None,
    path: Path,
) -> QueuedTask:
    queue = load_task_queue(path)
    requested_payload = payload or {}
    for existing in queue.tasks:
        if (
            existing.kind == kind
            and existing.tracker == tracker
            and existing.payload == requested_payload
            and existing.status in {QueuedTaskStatus.QUEUED, QueuedTaskStatus.RUNNING}
        ):
            return existing
    now = datetime.now(UTC)
    task = QueuedTask(
        task_id=uuid4().hex[:12],
        kind=kind,
        tracker=tracker,
        payload=requested_payload,
        created_at=now,
        updated_at=now,
    )
    queue.tasks.append(task)
    save_task_queue(queue, path)
    return task


def _json_mark_task_status(
    task_id: str,
    status: QueuedTaskStatus,
    last_error: str | None,
    path: Path,
) -> QueuedTask:
    queue = load_task_queue(path)
    for index, task in enumerate(queue.tasks):
        if task.task_id != task_id:
            continue
        updated = task.model_copy(
            update={"status": status, "updated_at": datetime.now(UTC), "last_error": last_error}
        )
        queue.tasks[index] = updated
        save_task_queue(queue, path)
        return updated
    raise ValueError(f"Unknown queued task: {task_id}")


def _is_json_path(path: Path) -> bool:
    return path.suffix.casefold() == ".json"


def _task_key(kind: QueuedTaskKind, tracker: TrackerName, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"kind": kind.value, "tracker": tracker.value, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _dt(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _task_values(task: QueuedTask) -> tuple[Any, ...]:
    return (
        task.task_id,
        task.kind.value,
        task.tracker.value,
        json.dumps(task.payload, sort_keys=True),
        task.status.value,
        _dt(task.created_at),
        _dt(task.updated_at),
        task.last_error,
        task.attempts,
        task.max_attempts,
        _dt(task.available_at),
        _dt(task.lease_until),
        task.correlation_id,
        task.idempotency_key,
    )


def _row_to_task(row: sqlite3.Row) -> QueuedTask:
    return QueuedTask(
        task_id=row["task_id"], kind=row["kind"], tracker=row["tracker"],
        payload=json.loads(row["payload_json"]), status=row["status"],
        created_at=_parse_dt(row["created_at"]), updated_at=_parse_dt(row["updated_at"]),
        last_error=row["last_error"], attempts=row["attempts"], max_attempts=row["max_attempts"],
        available_at=_parse_dt(row["available_at"]), lease_until=_parse_dt(row["lease_until"]),
        correlation_id=row["correlation_id"], idempotency_key=row["idempotency_key"],
    )


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY, kind TEXT NOT NULL, tracker TEXT NOT NULL,
            payload_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3, available_at TEXT, lease_until TEXT,
            correlation_id TEXT, idempotency_key TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_available ON tasks(status, available_at, created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_idempotency ON tasks(idempotency_key, status);
        CREATE TABLE IF NOT EXISTS task_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
        );
        """
    )


@contextmanager
def _connection(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    _initialize(connection)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _transaction(path: Path) -> Iterator[sqlite3.Connection]:
    with _connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _append_event(
    connection: sqlite3.Connection, task_id: str, event_type: str, payload: dict[str, Any]
) -> None:
    connection.execute(
        "INSERT INTO task_events(task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (task_id, event_type, json.dumps(payload, sort_keys=True), _dt(datetime.now(UTC))),
    )


def _recover_expired(connection: sqlite3.Connection, now: datetime) -> None:
    rows = connection.execute(
        "SELECT * FROM tasks WHERE status = 'running' AND lease_until <= ?", (_dt(now),)
    ).fetchall()
    for row in rows:
        status = "dead_letter" if row["attempts"] >= row["max_attempts"] else "retrying"
        connection.execute(
            """UPDATE tasks SET status = ?, updated_at = ?, available_at = ?, lease_until = NULL,
            last_error = COALESCE(last_error, 'Worker lease expired') WHERE task_id = ?""",
            (status, _dt(now), _dt(now), row["task_id"]),
        )
        _append_event(connection, row["task_id"], "lease_expired", {"next_status": status})
