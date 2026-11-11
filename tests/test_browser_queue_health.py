from datetime import UTC, datetime, timedelta

from backend_scout.config import TrackerName
from backend_scout.runtime_health import workflow_health
from backend_scout.task_queue import (
    BROWSER_TASK_KINDS,
    GENERAL_TASK_KINDS,
    QueuedTaskKind,
    claim_next_task,
    enqueue_task,
)


def test_browser_backlog_is_unhealthy_until_the_browser_lane_claims_it(tmp_path):
    db = tmp_path / "workflow.sqlite3"
    task = enqueue_task(
        QueuedTaskKind.PACKAGE_FORM_INSPECT, TrackerName.PRODUCTION, {}, path=db,
    )
    assert claim_next_task(db, allowed_kinds=GENERAL_TASK_KINDS, heartbeat_lane="general") is None
    later = datetime.now(UTC) + timedelta(minutes=3)
    stalled = workflow_health(db, later)["components"]["browser_queue"]
    assert not stalled["healthy"]
    assert stalled["oldest_task_id"] == task.task_id
    assert stalled["last_poll_at"] is None

    claimed = claim_next_task(db, allowed_kinds=BROWSER_TASK_KINDS, heartbeat_lane="browser")
    assert claimed.task_id == task.task_id
    healthy = workflow_health(db)["components"]["browser_queue"]
    assert healthy["healthy"]
    assert healthy["last_poll_at"]
