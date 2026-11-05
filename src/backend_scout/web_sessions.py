"""Owner browser credentials in the existing operational SQLite database.

The caller must authenticate the allowlisted actor before issuing a code, and
derive a stable client_key from trusted transport metadata, not request input.
Cookies, origins, HTTPS and request/response redaction belong to the transport.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUTH_SCHEMA_VERSION = 1
PAIRING_TTL_SECONDS = 5 * 60
SESSION_TTL_SECONDS = 12 * 60 * 60
PAIRING_WINDOW_SECONDS = 5 * 60
MAX_PAIRING_ATTEMPTS = 5
_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
_DUMMY_HASH = bytes(32)


class PairingRejected(ValueError):
    """Invalid, expired, consumed, unauthorized or rate-limited pairing request."""


@dataclass(frozen=True)
class OwnerSession:
    actor_id: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionCredentials(OwnerSession):
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)


def _digest(kind: bytes, value: str) -> bytes:
    return hashlib.sha256(kind + b"\0" + value.encode("utf-8")).digest()


def _secret_digest(kind: bytes, value: object) -> bytes | None:
    if not isinstance(value, str) or _SECRET_PATTERN.fullmatch(value) is None:
        return None
    return _digest(kind, value)


def _csrf_digest(token_hash: bytes, csrf_token: str) -> bytes:
    return hashlib.sha256(b"csrf\0" + token_hash + csrf_token.encode("ascii")).digest()


class OwnerSessionStore:
    """Hashed bearer credentials with atomic pairing and durable throttling.

    clock returns Unix seconds or a timezone-aware datetime. Session expiry is
    absolute, not sliding. A newly issued code replaces the actor's previous one.
    Rejected redemptions raise PairingRejected with a deliberately generic message.
    """

    def __init__(
        self,
        db_path: str | Path,
        allowed_actor_ids: set[str],
        clock: Callable[[], float | datetime] | None = None,
    ):
        if any(not isinstance(actor, str) or not actor.strip() for actor in allowed_actor_ids):
            raise ValueError("Owner allowlist must contain nonempty actor identities")
        self.db_path = Path(db_path).expanduser().resolve()
        self.allowed_actor_ids = frozenset(allowed_actor_ids)
        self._clock = clock if clock is not None else time.time
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._migrate()

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                raise ValueError("Authentication clock must be timezone-aware")
            value = value.timestamp()
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Authentication clock must be finite")
        return value

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _migrate(self) -> None:
        with self._connection(write=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS owner_auth_schema_versions "
                "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            versions = db.execute("SELECT version FROM owner_auth_schema_versions").fetchall()
            if versions:
                if [row[0] for row in versions] != [AUTH_SCHEMA_VERSION]:
                    raise ValueError("Owner authentication schema is unsupported by this release")
                return
            db.execute(
                """CREATE TABLE owner_auth_pairing_codes (
                    code_hash BLOB PRIMARY KEY CHECK(length(code_hash)=32),
                    actor_id TEXT NOT NULL, expires_at REAL NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE owner_auth_sessions (
                    token_hash BLOB PRIMARY KEY CHECK(length(token_hash)=32),
                    csrf_hash BLOB NOT NULL CHECK(length(csrf_hash)=32),
                    actor_id TEXT NOT NULL, expires_at REAL NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE owner_auth_pairing_clients (
                    client_hash BLOB PRIMARY KEY CHECK(length(client_hash)=32),
                    window_started_at REAL NOT NULL,
                    attempts INTEGER NOT NULL CHECK(attempts>0))"""
            )
            db.execute(
                "INSERT INTO owner_auth_schema_versions VALUES (?, ?)",
                (AUTH_SCHEMA_VERSION, self._now()),
            )

    def issue_pairing_code(self, actor_id: str) -> str:
        """Issue only after the caller has verified this actor's identity."""
        if not isinstance(actor_id, str) or actor_id not in self.allowed_actor_ids:
            raise PermissionError("Owner identity is not authorized")
        code = secrets.token_urlsafe(32)
        with self._connection(write=True) as db:
            now = self._now()
            db.execute(
                "DELETE FROM owner_auth_pairing_codes WHERE actor_id=? OR expires_at<=?",
                (actor_id, now),
            )
            db.execute(
                "INSERT INTO owner_auth_pairing_codes VALUES (?, ?, ?)",
                (_digest(b"pairing", code), actor_id, now + PAIRING_TTL_SECONDS),
            )
        return code

    @staticmethod
    def _record_attempt(db: sqlite3.Connection, client_hash: bytes, now: float) -> bool:
        db.execute(
            "DELETE FROM owner_auth_pairing_clients WHERE window_started_at<=?",
            (now - PAIRING_WINDOW_SECONDS,),
        )
        row = db.execute(
            "SELECT attempts FROM owner_auth_pairing_clients WHERE client_hash=?", (client_hash,)
        ).fetchone()
        if row is not None and row["attempts"] >= MAX_PAIRING_ATTEMPTS:
            return False
        db.execute(
            "INSERT INTO owner_auth_pairing_clients VALUES (?, ?, 1) "
            "ON CONFLICT(client_hash) DO UPDATE SET attempts=attempts+1",
            (client_hash, now),
        )
        return True

    def redeem_pairing_code(self, code: str, client_key: str) -> SessionCredentials:
        """Consume a code once; every attempt, including success, uses client budget."""
        if not isinstance(client_key, str) or not client_key or len(client_key) > 1024:
            raise PairingRejected("Pairing request rejected")
        code_hash = _secret_digest(b"pairing", code)
        credentials = None
        with self._connection(write=True) as db:
            now = self._now()
            permitted = self._record_attempt(db, _digest(b"client", client_key), now)
            row = db.execute(
                "SELECT * FROM owner_auth_pairing_codes WHERE code_hash=?",
                (code_hash if code_hash is not None else _DUMMY_HASH,),
            ).fetchone()
            matches = secrets.compare_digest(
                row["code_hash"] if row is not None else _DUMMY_HASH,
                code_hash if code_hash is not None else _DUMMY_HASH,
            )
            if (
                permitted
                and matches
                and code_hash is not None
                and row is not None
                and row["expires_at"] > now
                and row["actor_id"] in self.allowed_actor_ids
            ):
                token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                token_hash = _digest(b"session", token)
                expires_at = now + SESSION_TTL_SECONDS
                db.execute("DELETE FROM owner_auth_pairing_codes WHERE code_hash=?", (code_hash,))
                db.execute(
                    "INSERT INTO owner_auth_sessions VALUES (?, ?, ?, ?)",
                    (token_hash, _csrf_digest(token_hash, csrf_token), row["actor_id"], expires_at),
                )
                credentials = SessionCredentials(
                    actor_id=row["actor_id"],
                    expires_at=datetime.fromtimestamp(expires_at, UTC),
                    token=token,
                    csrf_token=csrf_token,
                )
        # Raise after commit so rejected attempts cannot roll back the throttle.
        if credentials is None:
            raise PairingRejected("Pairing request rejected")
        return credentials

    def _session_row(self, db: sqlite3.Connection, token_hash: bytes) -> sqlite3.Row | None:
        row = db.execute(
            "SELECT * FROM owner_auth_sessions WHERE token_hash=?", (token_hash,)
        ).fetchone()
        matches = secrets.compare_digest(
            row["token_hash"] if row is not None else _DUMMY_HASH, token_hash
        )
        if (
            matches
            and row is not None
            and row["expires_at"] > self._now()
            and row["actor_id"] in self.allowed_actor_ids
        ):
            return row
        return None

    def authenticate(self, token: str) -> OwnerSession | None:
        token_hash = _secret_digest(b"session", token)
        if token_hash is None:
            return None
        with self._connection() as db:
            row = self._session_row(db, token_hash)
        if row is None:
            return None
        return OwnerSession(row["actor_id"], datetime.fromtimestamp(row["expires_at"], UTC))

    def verify_csrf(self, session_token: str, csrf_token: str) -> bool:
        token_hash = _secret_digest(b"session", session_token)
        if token_hash is None or _secret_digest(b"csrf", csrf_token) is None:
            return False
        with self._connection() as db:
            row = self._session_row(db, token_hash)
        matches = secrets.compare_digest(
            row["csrf_hash"] if row is not None else _DUMMY_HASH,
            _csrf_digest(token_hash, csrf_token),
        )
        return matches and row is not None

    def revoke(self, token: str) -> None:
        """Idempotently revoke one session without exposing its bearer token."""
        token_hash = _secret_digest(b"session", token)
        if token_hash is not None:
            with self._connection(write=True) as db:
                db.execute("DELETE FROM owner_auth_sessions WHERE token_hash=?", (token_hash,))
