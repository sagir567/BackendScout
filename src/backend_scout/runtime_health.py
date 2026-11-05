"""Read-only checks of business progress, separate from process liveness."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


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
    except (sqlite3.Error, ValueError, TypeError):
        return {"healthy": False, "components": {}, "problem": "Workflow database is unreadable"}
    return {
        "healthy": all(item["healthy"] for item in components.values()),
        "components": components,
    }
