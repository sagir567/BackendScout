"""Own visible portal browsers for the lifetime of the single browser worker.

Create one ``PortalSessionManager(profile_root, max_sessions=3,
playwright_factory=None)`` per worker, optionally using it as a context manager.
Entering is lazy; the first acquire starts ``sync_playwright().start()``. An
injected factory must return the same start()/__exit__() interface; start()
returns an object with chromium.launch_persistent_context() and stop().

``acquire(application_id, tracker, destination, cv_sha256, answers_fingerprint)``
returns a frozen ``PortalSession`` with ``page``, ``context``, ``profile_path``,
``application_id``, ``tracker`` and ``binding``. Binding contains the exact
``official_destination``, lowercase ``cv_sha256`` and opaque, nonblank
``answers_fingerprint``. Callers establish approval and hash actual CV bytes;
this owner does neither. A live session is returned by identity, with no page
navigation, reload, filling, upload or submission. Only a fresh/reopened page
navigates to the bound HTTPS destination, without clicking any controls.

``close_session(application_id, tracker) -> bool`` explicitly closes just that
context (False if absent), releasing its binding and lock, not deleting its
profile. ``close() -> None`` shuts down all contexts and Playwright; it is
idempotent and terminal. Use it at worker shutdown, not at the end of each task.
All public operations must run on the same thread after first use. Invalid
arguments raise ValueError before browser/profile I/O. Operational failures have
a stable ``PortalSessionError.reason`` and omit destination/answer contents.

Reason codes: binding_changed, session_limit, profile_locked, unsafe_profile,
browser_start_failed, startup_cleanup_required, session_open_failed,
session_unavailable, session_cleanup_required, session_close_failed,
shutdown_failed, wrong_thread and manager_closed.
"""

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Thread, current_thread
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlsplit

from backend_scout.config import TrackerName

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

__all__ = ["PortalSession", "PortalSessionBinding", "PortalSessionError", "PortalSessionManager"]


class PortalSessionError(RuntimeError):
    """A safe-to-display failure; inspect ``reason`` for programmatic handling."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PortalSessionBinding:
    official_destination: str
    cv_sha256: str
    answers_fingerprint: str


@dataclass(frozen=True)
class PortalSession:
    application_id: str
    tracker: TrackerName
    binding: PortalSessionBinding
    profile_path: Path
    context: "BrowserContext"
    page: "Page"


@dataclass
class _OwnedContext:
    binding: PortalSessionBinding
    context: "BrowserContext"
    lock_fd: int | None
    session: PortalSession | None = None
    closed: bool = False
    cleanup_required: bool = False

    def release_lock(self) -> None:
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def on_close(self, *_: object) -> None:
        self.closed = True
        self.release_lock()


class PortalSessionManager:
    """Thread-affine, bounded owner; never evicts a form or authorizes delivery.

    Closed contexts keep their immutable binding until explicit close_session()
    or shutdown, but do not consume capacity. A user-closed page is reopened in
    its live context; a user-closed browser is relaunched with the same profile.
    Reacquire after either closure to obtain the replacement session/page.
    """

    def __init__(
        self,
        profile_root: Path | str,
        max_sessions: int = 3,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        self._profile_root = Path(profile_root).expanduser().resolve()
        self._max_sessions = max_sessions
        self._factory = playwright_factory
        self._playwright: Any = None
        self._launcher: Any = None
        self._sessions: dict[tuple[TrackerName, str], _OwnedContext] = {}
        self._thread: Thread | None = None
        self._closed = False

    def __enter__(self) -> Self:
        self._check_available()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except PortalSessionError:
            if exc is None:
                raise
            exc.add_note(
                "Portal browser shutdown failed; retry manager.close() on its owner thread."
            )

    def acquire(
        self,
        application_id: str,
        tracker: TrackerName | str,
        destination: str,
        cv_sha256: str,
        answers_fingerprint: str,
        *,
        navigate: bool = True,
    ) -> PortalSession:
        """Get the unchanged live page, or open only the approved entry on closure.

        Changed binding: ``binding_changed``. Full capacity: ``session_limit``.
        Both fail without changing, navigating or closing any existing page.
        """
        self._check_available()
        key = _session_key(application_id, tracker)
        binding = _binding(destination, cv_sha256, answers_fingerprint)
        owned = self._sessions.get(key)
        if owned is not None:
            if owned.binding != binding:
                raise PortalSessionError(
                    "binding_changed",
                    "The portal session is bound to a different destination, CV or answers. "
                    "Explicitly close it before opening a revised package.",
                )
            if not owned.closed:
                if owned.cleanup_required:
                    raise PortalSessionError(
                        "session_cleanup_required",
                        "Explicitly close the failed portal session first.",
                    )
                assert owned.session is not None
                if self._page_is_open(owned):
                    return owned.session
                if not owned.closed:
                    reopened = self._reopen_page(owned, navigate=navigate)
                    if reopened is not None:
                        return reopened
        # Sync Playwright dispatches user-close events only while its loop runs.
        # Zero-time waits pump events without navigating or touching form values.
        for item in self._sessions.values():
            if not item.closed and item.session is not None:
                with suppress(PortalSessionError):
                    self._page_is_open(item)
        if sum(not item.closed for item in self._sessions.values()) >= self._max_sessions:
            raise PortalSessionError(
                "session_limit",
                "The portal session limit is reached. Explicitly close a session first.",
            )
        return self._open_context(key, binding, navigate=navigate)

    def close_session(self, application_id: str, tracker: TrackerName | str) -> bool:
        """Close one context and forget its binding; retain profile files for reuse.

        On ``session_close_failed``, retain ownership/lock and allow a close retry.
        Calling this after successful shutdown returns False.
        """
        self._check_thread()
        key = _session_key(application_id, tracker)
        owned = self._sessions.get(key)
        if owned is None:
            return False
        try:
            self._close_context(owned)
        except Exception as exc:
            raise PortalSessionError(
                "session_close_failed",
                "Could not close the portal session; its profile remains owned.",
            ) from exc
        del self._sessions[key]
        return True

    def close(self) -> None:
        """Attempt every cleanup, even if one fails; retry on the owner thread.

        ``shutdown_failed`` never permits another acquire. Unclosed resources
        remain owned until a subsequent close succeeds (no unsafe lock release).
        """
        self._check_thread()
        self._closed = True
        errors: list[Exception] = []
        for owned in self._sessions.values():
            try:
                self._close_context(owned)
            except Exception as exc:  # noqa: BLE001 - every independently owned resource needs cleanup.
                errors.append(exc)
        try:
            self._stop_playwright()
        except Exception as exc:  # noqa: BLE001 - preserve ownership for a shutdown retry.
            errors.append(exc)
        else:
            for owned in self._sessions.values():
                owned.on_close()
            self._sessions.clear()
        if errors:
            raise PortalSessionError(
                "shutdown_failed",
                "Portal browser shutdown failed; retry close on the owner thread.",
            ) from errors[0]

    def _check_thread(self) -> None:
        thread = current_thread()
        if self._thread is None:
            self._thread = thread
        elif self._thread is not thread:
            raise PortalSessionError(
                "wrong_thread",
                "Portal sessions must be used on their owning browser worker thread.",
            )

    def _check_available(self) -> None:
        self._check_thread()
        if self._closed:
            raise PortalSessionError("manager_closed", "The portal session manager is shut down.")

    def _start_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        if self._launcher is not None:
            raise PortalSessionError(
                "startup_cleanup_required",
                "Playwright startup cleanup failed; close the manager first.",
            )
        try:
            factory = self._factory
            if factory is None:
                from playwright.sync_api import sync_playwright

                factory = sync_playwright
            self._launcher = factory()
            self._playwright = self._launcher.start()
        except BaseException as exc:
            with suppress(Exception):
                self._stop_playwright()
            if not isinstance(exc, Exception):
                raise
            raise PortalSessionError("browser_start_failed", "Could not start Playwright.") from exc
        self._launcher = None
        return self._playwright

    def _stop_playwright(self) -> None:
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        elif self._launcher is not None:
            self._launcher.__exit__(None, None, None)
        self._launcher = None

    def _open_context(
        self, key: tuple[TrackerName, str], binding: PortalSessionBinding, *, navigate: bool = True
    ) -> PortalSession:
        tracker, application_id = key
        profile_path, descriptor = self._lock_profile(tracker, application_id)
        owned = None
        try:
            playwright = self._start_playwright()
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), headless=False, accept_downloads=False,
                service_workers="block",
                env={key: value for key, value in os.environ.items()
                     if key in {"PATH", "HOME", "TMPDIR", "LANG", "DISPLAY", "XAUTHORITY"}},
            )
            owned = _OwnedContext(binding, context, descriptor)
            self._sessions[key] = owned
            context.on("close", owned.on_close)
            page = context.pages[0] if context.pages else context.new_page()
            owned.session = PortalSession(
                application_id, tracker, binding, profile_path, context, page
            )
            if navigate:
                page.goto(binding.official_destination, wait_until="domcontentloaded")
            return owned.session
        except BaseException as exc:
            if owned is None:
                os.close(descriptor)
            else:
                owned.cleanup_required = True
                with suppress(Exception):
                    self._close_context(owned)
            if not any(not item.closed for item in self._sessions.values()):
                with suppress(Exception):
                    self._stop_playwright()
            if not isinstance(exc, Exception) or isinstance(exc, PortalSessionError):
                raise
            raise PortalSessionError(
                "session_open_failed",
                "Could not open the approved portal entry; no submit was attempted.",
            ) from exc

    @staticmethod
    def _page_is_open(owned: _OwnedContext) -> bool:
        assert owned.session is not None
        try:
            owned.session.page.wait_for_timeout(0)
        except Exception as exc:
            if not owned.closed and not owned.session.page.is_closed():
                raise PortalSessionError(
                    "session_unavailable", "Could not check the portal page; no form was changed."
                ) from exc
        return not owned.closed and not owned.session.page.is_closed()

    @staticmethod
    def _reopen_page(owned: _OwnedContext, *, navigate: bool = True) -> PortalSession | None:
        assert owned.session is not None
        page = None
        try:
            page = owned.context.new_page()
            if navigate:
                page.goto(owned.binding.official_destination, wait_until="domcontentloaded")
        except BaseException as exc:
            if page is not None:
                owned.cleanup_required = True
                with suppress(Exception):
                    page.close()
                    owned.cleanup_required = False
            if not isinstance(exc, Exception):
                raise
            if owned.closed:
                return None
            raise PortalSessionError(
                "session_open_failed",
                "Could not reopen the approved portal entry; no submit was attempted.",
            ) from exc
        owned.session = replace(owned.session, page=page)
        return owned.session

    @staticmethod
    def _close_context(owned: _OwnedContext) -> None:
        if not owned.closed:
            owned.context.close()
        owned.on_close()

    def _lock_profile(self, tracker: TrackerName, application_id: str) -> tuple[Path, int]:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", application_id).strip("-_")[:48] or "application"
        digest = hashlib.sha256(application_id.encode("utf-8")).hexdigest()
        profile_path = self._profile_root / tracker.value / f"{slug}-{digest}"
        directory = None
        descriptor = None
        try:
            self._profile_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory = os.open(self._profile_root, flags)
            for part in (tracker.value, profile_path.name):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(
                ".backend-scout-session.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PortalSessionError(
                    "unsafe_profile", "The portal profile lock is not a regular file."
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PortalSessionError(
                    "profile_locked",
                    "The portal profile is already owned by another browser session.",
                ) from exc
            return profile_path, descriptor
        except BaseException as exc:
            if descriptor is not None:
                os.close(descriptor)
            if not isinstance(exc, Exception) or isinstance(exc, PortalSessionError):
                raise
            raise PortalSessionError(
                "unsafe_profile", "Cannot safely access the dedicated portal profile."
            ) from exc
        finally:
            if directory is not None:
                os.close(directory)


def _session_key(application_id: str, tracker: TrackerName | str) -> tuple[TrackerName, str]:
    _require_text(application_id, "application_id")
    return TrackerName(tracker), application_id


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{name} must be UTF-8 text") from exc


def _binding(destination: str, cv_sha256: str, answers_fingerprint: str) -> PortalSessionBinding:
    _require_text(destination, "destination")
    _require_text(answers_fingerprint, "answers_fingerprint")
    if not isinstance(cv_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", cv_sha256):
        raise ValueError("cv_sha256 must contain exactly 64 hexadecimal characters")
    if "\\" in destination or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in destination
    ):
        raise ValueError("destination must be an HTTPS URL without whitespace")
    parsed = urlsplit(destination)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.netloc:
        raise ValueError("destination must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("destination must not contain credentials")
    _ = parsed.port  # Reject malformed/out-of-range ports without changing the approved URL.
    return PortalSessionBinding(destination, cv_sha256.lower(), answers_fingerprint)
