"""DBOS-backed durable schedules for the local BackendScout runtime."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from dbos import DBOS

from backend_scout.config import TrackerName
from backend_scout.task_queue import QueuedTaskKind, enqueue_task

DAILY_SCHEDULE_NAME = "backendscout-daily-production-scout"
MAILBOX_SCHEDULE_NAME = "backendscout-production-mailbox"


@DBOS.step(retries_allowed=True, interval_seconds=2, max_attempts=5, backoff_rate=2)
def enqueue_scheduled_task(
    kind: str,
    tracker: str,
    payload: dict[str, Any],
    database_path: str,
    idempotency_key: str,
) -> str:
    task = enqueue_task(
        QueuedTaskKind(kind),
        TrackerName(tracker),
        payload,
        Path(database_path),
        idempotency_key=idempotency_key,
    )
    return task.task_id


@DBOS.workflow(name="scheduled_production_scout")
def scheduled_production_scout(scheduled_time: datetime, context: dict[str, Any]) -> str:
    date_key = scheduled_time.date().isoformat()
    payload = {"chat_id": context.get("chat_id"), "scheduled_for": scheduled_time.isoformat()}
    return enqueue_scheduled_task(
        QueuedTaskKind.SCOUT_TODAY.value,
        TrackerName.PRODUCTION.value,
        payload,
        str(context["database_path"]),
        f"schedule:scout:{date_key}",
    )


@DBOS.workflow(name="scheduled_production_mailbox")
def scheduled_production_mailbox(scheduled_time: datetime, context: dict[str, Any]) -> str:
    interval_key = scheduled_time.replace(second=0, microsecond=0).isoformat()
    payload = {"chat_id": context.get("chat_id"), "scheduled_for": scheduled_time.isoformat()}
    return enqueue_scheduled_task(
        QueuedTaskKind.MAILBOX_SCAN.value,
        TrackerName.PRODUCTION.value,
        payload,
        str(context["database_path"]),
        f"schedule:mailbox:{interval_key}",
    )


def launch_durable_schedules(database_path: Path, chat_id: int | None) -> None:
    """Launch DBOS and register the production schedules in local SQLite."""
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    DBOS(
        config={
            "name": "backendscout",
            "system_database_url": f"sqlite:///{database_path}",
        }
    )
    DBOS.launch()
    context = {"database_path": str(database_path), "chat_id": chat_id}
    DBOS.apply_schedules(
        [
            {
                "schedule_name": DAILY_SCHEDULE_NAME,
                "workflow_fn": scheduled_production_scout,
                "schedule": "0 8 * * *",
                "context": context,
                "automatic_backfill": True,
                "cron_timezone": "Asia/Hebron",
            },
            {
                "schedule_name": MAILBOX_SCHEDULE_NAME,
                "workflow_fn": scheduled_production_mailbox,
                "schedule": "*/15 * * * *",
                "context": context,
                "automatic_backfill": False,
                "cron_timezone": "Asia/Hebron",
            },
        ]
    )


def stop_durable_runtime() -> None:
    DBOS.destroy(workflow_completion_timeout_sec=10)
