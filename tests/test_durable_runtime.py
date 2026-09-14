from datetime import UTC, datetime

from backend_scout import durable_runtime


def test_scheduled_scout_uses_one_idempotency_key_per_day(monkeypatch) -> None:
    captured = []

    def fake_enqueue(*args):
        captured.append(args)
        return "task-1"

    monkeypatch.setattr(durable_runtime, "enqueue_scheduled_task", fake_enqueue)
    result = durable_runtime.scheduled_production_scout.__wrapped__(
        datetime(2026, 9, 15, 8, tzinfo=UTC),
        {"database_path": "/tmp/runtime.sqlite3", "chat_id": 123},
    )

    assert result == "task-1"
    assert captured[0][-1] == "schedule:scout:2026-09-15"
    assert captured[0][2]["chat_id"] == 123


def test_scheduled_mailbox_has_interval_idempotency_key(monkeypatch) -> None:
    captured = []

    def fake_enqueue(*args):
        captured.append(args)
        return "task-2"

    monkeypatch.setattr(durable_runtime, "enqueue_scheduled_task", fake_enqueue)
    durable_runtime.scheduled_production_mailbox.__wrapped__(
        datetime(2026, 9, 15, 8, 15, 42, tzinfo=UTC),
        {"database_path": "/tmp/runtime.sqlite3", "chat_id": 123},
    )

    assert captured[0][-1] == "schedule:mailbox:2026-09-15T08:15:00+00:00"
