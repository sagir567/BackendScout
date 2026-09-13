from pathlib import Path

from backend_scout.gmail import GmailMessageSummary
from backend_scout.mailbox_state import mark_messages_processed, unseen_messages


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
