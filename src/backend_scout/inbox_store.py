"""Durable idempotency records for external events."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def begin_inbox_event(
    database_path: Path,
    source: str,
    event_id: str,
    payload: dict[str, Any],
) -> bool:
    """Record an event and return false only when it already completed."""
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT status FROM inbox_events WHERE source = ? AND event_id = ?",
            (source, event_id),
        ).fetchone()
        if row is not None and row[0] == "completed":
            return False
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO inbox_events (
                source, event_id, payload_json, status, attempts, received_at, updated_at
            ) VALUES (?, ?, ?, 'processing', 1, ?, ?)
            ON CONFLICT(source, event_id) DO UPDATE SET
                status = 'processing', attempts = attempts + 1, updated_at = excluded.updated_at
            """,
            (source, event_id, json.dumps(payload, sort_keys=True), now, now),
        )
        connection.commit()
        return True


def finish_inbox_event(
    database_path: Path,
    source: str,
    event_id: str,
    *,
    error: str | None = None,
) -> None:
    with _connection(database_path) as connection:
        connection.execute(
            """UPDATE inbox_events SET status = ?, last_error = ?, updated_at = ?
            WHERE source = ? AND event_id = ?""",
            (
                "failed" if error else "completed",
                error,
                datetime.now(UTC).isoformat(),
                source,
                event_id,
            ),
        )
        connection.commit()


def inbox_event_status(database_path: Path, source: str, event_id: str) -> str | None:
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT status FROM inbox_events WHERE source = ? AND event_id = ?",
            (source, event_id),
        ).fetchone()
    return None if row is None else str(row[0])


def _connection(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox_events (
            source TEXT NOT NULL,
            event_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            received_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_error TEXT,
            PRIMARY KEY (source, event_id)
        )
        """
    )
    return connection
