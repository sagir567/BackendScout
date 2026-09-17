from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend_scout.gmail import GmailMessageSummary
from backend_scout.mailbox_state import (
    clear_gmail_auth_alert,
    gmail_auth_alert_due,
    mark_gmail_auth_alert_sent,
    mark_messages_processed,
    unseen_messages,
)


def _message(message_id: str) -> GmailMessageSummary:
    return GmailMessageSummary(
        message_id=message_id,
        thread_id=None,
        from_header="jobs@example.com",
        subject="Application update",
        date_header="",
        snippet="Update",
    )


def test_mailbox_checkpoint_filters_messages_after_success(tmp_path: Path) -> None:
    path = tmp_path / "processed.json"
    messages = [_message("one"), _message("two")]

    assert unseen_messages(messages, path) == messages
    mark_messages_processed([messages[0]], path)

    assert unseen_messages(messages, path) == [messages[1]]


def test_invalid_mailbox_checkpoint_fails_open(tmp_path: Path) -> None:
    path = tmp_path / "processed.json"
    path.write_text("not-json", encoding="utf-8")

    assert unseen_messages([_message("one")], path) == [_message("one")]


def test_gmail_auth_alert_has_cooldown_and_can_be_cleared(tmp_path: Path) -> None:
    path = tmp_path / "gmail_auth_attention.json"
    now = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)

    assert gmail_auth_alert_due(path, now=now)

    mark_gmail_auth_alert_sent(path, now=now)

    assert not gmail_auth_alert_due(path, now=now + timedelta(hours=23))
    assert gmail_auth_alert_due(path, now=now + timedelta(hours=24))

    clear_gmail_auth_alert(path)

    assert gmail_auth_alert_due(path, now=now)
