from pathlib import Path

from backend_scout.inbox_store import (
    begin_inbox_event,
    finish_inbox_event,
    inbox_event_status,
)


def test_completed_inbox_event_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"

    assert begin_inbox_event(database, "telegram", "42", {"update_id": 42})
    finish_inbox_event(database, "telegram", "42")

    assert inbox_event_status(database, "telegram", "42") == "completed"
    assert not begin_inbox_event(database, "telegram", "42", {"update_id": 42})


def test_failed_inbox_event_can_be_retried(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    assert begin_inbox_event(database, "telegram", "43", {"update_id": 43})
    finish_inbox_event(database, "telegram", "43", error="temporary failure")

    assert inbox_event_status(database, "telegram", "43") == "failed"
    assert begin_inbox_event(database, "telegram", "43", {"update_id": 43})
