"""Private checkpoint storage for the recurring Gmail status watcher."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend_scout.gmail import GmailMessageSummary

DEFAULT_MAILBOX_STATE_PATH = Path("data/mailbox/processed_status_messages.json")
MAX_PROCESSED_MESSAGE_IDS = 5_000
GMAIL_AUTH_ALERT_COOLDOWN = timedelta(hours=24)


def unseen_messages(
    messages: list[GmailMessageSummary],
    path: Path = DEFAULT_MAILBOX_STATE_PATH,
) -> list[GmailMessageSummary]:
    processed = load_processed_message_ids(path)
    return [message for message in messages if message.message_id not in processed]


def load_processed_message_ids(path: Path = DEFAULT_MAILBOX_STATE_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    values = payload.get("processed_message_ids") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str) and value}


def mark_messages_processed(
    messages: list[GmailMessageSummary],
    path: Path = DEFAULT_MAILBOX_STATE_PATH,
) -> None:
    existing = list(load_processed_message_ids(path))
    ordered = list(dict.fromkeys([*existing, *(message.message_id for message in messages)]))
    retained = ordered[-MAX_PROCESSED_MESSAGE_IDS:]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"processed_message_ids": retained}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def gmail_auth_alert_path(mailbox_state_path: Path = DEFAULT_MAILBOX_STATE_PATH) -> Path:
    return mailbox_state_path.with_name("gmail_auth_attention.json")


def gmail_auth_alert_due(
    path: Path,
    *,
    now: datetime | None = None,
    cooldown: timedelta = GMAIL_AUTH_ALERT_COOLDOWN,
) -> bool:
    current = now or datetime.now(UTC)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_timestamp = payload.get("last_notified_at") if isinstance(payload, dict) else None
        last_notified = datetime.fromisoformat(raw_timestamp) if isinstance(raw_timestamp, str) else None
    except (json.JSONDecodeError, OSError, ValueError):
        return True
    if last_notified is None:
        return True
    if last_notified.tzinfo is None:
        last_notified = last_notified.replace(tzinfo=UTC)
    return current - last_notified >= cooldown


def mark_gmail_auth_alert_sent(path: Path, *, now: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"last_notified_at": (now or datetime.now(UTC)).isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def clear_gmail_auth_alert(path: Path) -> None:
    path.unlink(missing_ok=True)
