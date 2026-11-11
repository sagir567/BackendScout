"""Read-only checks of business progress, separate from process liveness."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend_scout.task_queue import BROWSER_TASK_KINDS


def _browser_queue_health(db: sqlite3.Connection, now: datetime) -> dict:
    kinds = tuple(sorted(kind.value for kind in BROWSER_TASK_KINDS))
    placeholders = ", ".join("?" for _ in kinds)
    oldest = db.execute(
        f"SELECT task_id, COALESCE(available_at, created_at) AS ready_at FROM tasks "
        f"WHERE kind IN ({placeholders}) AND status IN ('queued', 'retrying') "
        "AND COALESCE(available_at, created_at) <= ? ORDER BY ready_at LIMIT 1",
        (*kinds, now.isoformat()),
    ).fetchone()
    heartbeat = None
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_worker_heartbeat'"
    ).fetchone():
        heartbeat = db.execute(
            "SELECT MAX(polled_at) FROM task_worker_heartbeat WHERE lane IN ('browser', 'all')"
        ).fetchone()[0]
    age = max(0, int((now - datetime.fromisoformat(oldest["ready_at"])).total_seconds())) if oldest else 0
    return {
        "healthy": age < 120,
        "latest_status": "stalled" if age >= 120 else "waiting" if oldest else "idle",
        "oldest_task_id": oldest["task_id"] if oldest else None,
        "oldest_available_seconds": age,
        "last_poll_at": heartbeat,
    }


def workflow_health(database_path: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    components = {}
    if not database_path.is_file():
        return {"healthy": False, "components": {}, "problem": "Workflow database is missing"}
    try:
        with sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            for kind, maximum_age in (
                ("scout_today", timedelta(hours=26)),
                ("mailbox_scan", timedelta(minutes=50)),
            ):
                row = db.execute(
                    "SELECT status, updated_at FROM tasks WHERE kind=? AND tracker='production' "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                    (kind,),
                ).fetchone()
                success = db.execute(
                    "SELECT updated_at FROM tasks WHERE kind=? AND tracker='production' "
                    "AND status='done' ORDER BY updated_at DESC LIMIT 1",
                    (kind,),
                ).fetchone()
                outcome = db.execute(
                    "SELECT status FROM tasks WHERE kind=? AND tracker='production' "
                    "AND (status NOT IN ('queued', 'running') OR last_error IS NOT NULL) "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                    (kind,),
                ).fetchone()
                last_success = datetime.fromisoformat(success[0]) if success else None
                status = row["status"] if row else "never_run"
                outcome_status = outcome["status"] if outcome else "never_completed"
                healthy = bool(
                    last_success
                    and timedelta(0) <= now - last_success <= maximum_age
                    and outcome_status == "done"
                )
                components[kind] = {
                    "healthy": healthy,
                    "latest_status": status,
                    "latest_outcome": outcome_status,
                    "last_success_at": success[0] if success else None,
                }
            components["browser_queue"] = _browser_queue_health(db, now)
    except (sqlite3.Error, ValueError, TypeError):
        return {"healthy": False, "components": {}, "problem": "Workflow database is unreadable"}
    return {
        "healthy": all(item["healthy"] for item in components.values()),
        "components": components,
    }
