"""Synthetic browser objects only: no Playwright driver, UI, network or employer state."""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from backend_scout.config import TrackerName
from backend_scout.portal_sessions import PortalSessionError, PortalSessionManager

DESTINATION = "https://careers.example.test/jobs/123/apply?source=approved"
CV_SHA256 = "ab" * 32
ANSWERS = "reviewed-answers-v1"


class FakePage:
    def __init__(self) -> None:
        self.closed = False
        self.url = "about:blank"
        self.values: dict[str, str] = {}
        self.uploads: list[str] = []
        self.navigations: list[tuple[str, str]] = []
        self.goto_error: BaseException | None = None
        self.close_error: Exception | None = None
        self.heartbeat_error: Exception | None = None
        self.on_heartbeat = None
        self.heartbeat_calls: list[int] = []

    def is_closed(self) -> bool:
        return self.closed

    def goto(self, url: str, *, wait_until: str) -> None:
        self.navigations.append((url, wait_until))
        if self.goto_error:
            raise self.goto_error
        self.url = url

    def close(self) -> None:
        if self.close_error:
            raise self.close_error
        self.closed = True

    def wait_for_timeout(self, milliseconds: int) -> None:
        assert milliseconds == 0
        self.heartbeat_calls.append(milliseconds)
        if self.on_heartbeat is not None:
            callback, self.on_heartbeat = self.on_heartbeat, None
            callback()
        if self.heartbeat_error:
            raise self.heartbeat_error
        if self.closed:
            raise RuntimeError("Synthetic target closed")


class FakeContext:
    def __init__(self, *, initial_page: bool = True) -> None:
        self.all_pages = [FakePage()] if initial_page else []
        self.new_pages: list[FakePage] = []
        self.callbacks: list = []
        self.closed = False
        self.close_calls = 0
        self.close_error: Exception | None = None
        self.new_page_error: Exception | None = None

    @property
    def pages(self) -> list[FakePage]:
        return [page for page in self.all_pages if not page.closed]

    def new_page(self) -> FakePage:
        assert not self.closed
        if self.new_page_error:
            raise self.new_page_error
        page = self.new_pages.pop(0) if self.new_pages else FakePage()
        self.all_pages.append(page)
        return page

    def on(self, event: str, callback) -> None:
        assert event == "close"
        self.callbacks.append(callback)

    def user_close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for page in self.all_pages:
            page.closed = True
        for callback in self.callbacks:
            callback(self)

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error:
            raise self.close_error
        self.user_close()


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = self
        self.contexts: list[FakeContext] = []
        self.pending_contexts: list[FakeContext] = []
        self.launches: list[tuple[str, bool, bool]] = []
        self.launch_error: Exception | None = None
        self.stop_error: Exception | None = None
        self.stop_calls = 0

    def launch_persistent_context(
        self, profile: str, *, headless: bool, accept_downloads: bool,
        service_workers: str, env: dict,
    ) -> FakeContext:
        assert service_workers == "block"
        assert not any("TOKEN" in key or "KEY" in key or "SECRET" in key for key in env)
        self.launches.append((profile, headless, accept_downloads))
        if self.launch_error:
            raise self.launch_error
        context = self.pending_contexts.pop(0) if self.pending_contexts else FakeContext()
        self.contexts.append(context)
        return context

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_error:
            raise self.stop_error
        for context in self.contexts:
            context.user_close()


class FakeFactory:
    def __init__(self) -> None:
        self.playwright = FakePlaywright()
        self.calls = 0
        self.start_calls = 0
        self.exit_calls = 0
        self.factory_error: Exception | None = None
        self.start_error: BaseException | None = None
        self.exit_error: Exception | None = None

    def __call__(self):
        self.calls += 1
        if self.factory_error:
            raise self.factory_error
        return self

    def start(self) -> FakePlaywright:
        self.start_calls += 1
        if self.start_error:
            raise self.start_error
        return self.playwright

    def __exit__(self, *_: object) -> None:
        self.exit_calls += 1
        if self.exit_error:
            raise self.exit_error


def acquire(manager: PortalSessionManager, application_id: str = "application-123", **overrides):
    return manager.acquire(
        **{
            "application_id": application_id,
            "tracker": TrackerName.TEST,
            "destination": DESTINATION,
            "cv_sha256": CV_SHA256,
            "answers_fingerprint": ANSWERS,
        }
        | overrides
    )


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def manager(tmp_path: Path, factory: FakeFactory):
    with PortalSessionManager(tmp_path / "profiles", playwright_factory=factory) as owner:
        yield owner


def test_constructor_and_context_entry_are_lazy(tmp_path: Path, factory: FakeFactory) -> None:
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory):
        assert factory.calls == factory.start_calls == factory.playwright.stop_calls == 0
        assert not root.exists()
    assert factory.playwright.stop_calls == 0


def test_default_factory_starts_lazily_without_launching_real_playwright(
    tmp_path: Path, monkeypatch, factory: FakeFactory
) -> None:
    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=factory)
    )
    with PortalSessionManager(tmp_path / "profiles") as manager:
        assert factory.calls == 0
        session = acquire(manager)
        assert factory.start_calls == 1
        assert factory.playwright.launches == [(str(session.profile_path), False, False)]


def test_repeated_tasks_keep_identical_page_values_upload_and_url(manager, factory) -> None:
    first = acquire(manager)
    first.page.values["extra_question"] = "A candidate's unsaved answer"
    first.page.uploads.append("approved-cv.pdf")
    first.page.url = "https://careers.example.test/temporary-verification"

    for _ in range(4):
        assert acquire(manager) is first
        assert not first.page.closed
        assert not first.context.closed
    assert first.page.values == {"extra_question": "A candidate's unsaved answer"}
    assert first.page.uploads == ["approved-cv.pdf"]
    assert first.page.url.endswith("/temporary-verification")
    assert first.page.navigations == [(DESTINATION, "domcontentloaded")]
    assert factory.start_calls == 1
    assert len(factory.playwright.launches) == 1
    assert factory.playwright.stop_calls == first.context.close_calls == 0


def test_session_and_binding_are_immutable(manager) -> None:
    session = acquire(manager)
    assert session.application_id == "application-123"
    assert session.tracker == TrackerName.TEST
    assert session.binding.official_destination == DESTINATION
    assert session.binding.cv_sha256 == CV_SHA256
    assert session.binding.answers_fingerprint == ANSWERS
    with pytest.raises(FrozenInstanceError):
        session.binding.cv_sha256 = "ff" * 32
    with pytest.raises(FrozenInstanceError):
        session.page = FakePage()


@pytest.mark.parametrize(
    "change",
    [
        {"destination": "https://careers.example.test/another-job"},
        {"destination": DESTINATION + "&changed=true"},
        {"cv_sha256": "cd" * 32},
        {"answers_fingerprint": "private-different-answer-fingerprint"},
        {"answers_fingerprint": ANSWERS + " "},
    ],
)
def test_changed_binding_fails_without_touching_in_progress_page(manager, factory, change) -> None:
    session = acquire(manager)
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager, **change)
    assert failure.value.reason == "binding_changed"
    for value in change.values():
        assert value not in str(failure.value)
    assert acquire(manager) is session
    assert session.context.close_calls == 0
    assert session.page.navigations == [(DESTINATION, "domcontentloaded")]
    assert len(factory.playwright.launches) == 1


def test_hash_hex_case_does_not_change_binding(manager) -> None:
    session = acquire(manager, cv_sha256=CV_SHA256.upper())
    assert session.binding.cv_sha256 == CV_SHA256
    assert acquire(manager) is session


def test_default_capacity_rejects_without_eviction(manager, factory) -> None:
    sessions = [acquire(manager, f"application-{index}") for index in range(3)]
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager, "application-4")
    assert failure.value.reason == "session_limit"
    assert len(factory.playwright.launches) == 3
    for index, session in enumerate(sessions):
        assert acquire(manager, f"application-{index}") is session
        assert session.context.close_calls == 0


@pytest.mark.parametrize("limit", [0, -1, True, 3.0, "3", None])
def test_invalid_capacity_fails_before_startup(tmp_path, factory, limit) -> None:
    with pytest.raises(ValueError, match="max_sessions"):
        PortalSessionManager(tmp_path / "profiles", limit, factory)
    assert factory.calls == 0
    assert not (tmp_path / "profiles").exists()


def test_explicit_close_is_targeted_and_allows_new_binding(manager, factory) -> None:
    first = acquire(manager)
    other = acquire(manager, "other-application")
    marker = first.profile_path / "synthetic-profile-data"
    marker.write_text("profile data", encoding="utf-8")

    assert manager.close_session(first.application_id, "test") is True
    assert manager.close_session(first.application_id, "test") is False
    assert first.context.close_calls == 1
    assert acquire(manager, "other-application") is other
    assert other.context.close_calls == factory.playwright.stop_calls == 0
    revised = acquire(manager, answers_fingerprint="revised-answers")
    assert revised.page is not first.page
    assert revised.profile_path == first.profile_path
    assert marker.read_text(encoding="utf-8") == "profile data"


def test_shutdown_is_idempotent_terminal_and_closes_all(manager, factory) -> None:
    sessions = [acquire(manager, "one"), acquire(manager, "two")]
    manager.close()
    manager.close()
    assert factory.playwright.stop_calls == 1
    assert all(session.context.close_calls == 1 for session in sessions)
    assert manager.close_session("one", "test") is False
    for operation in (lambda: acquire(manager), manager.__enter__):
        with pytest.raises(PortalSessionError) as failure:
            operation()
        assert failure.value.reason == "manager_closed"


def test_context_manager_closes_on_task_exception(tmp_path, factory) -> None:
    with (
        pytest.raises(ValueError, match="synthetic task failure"),
        PortalSessionManager(tmp_path / "profiles", playwright_factory=factory) as manager,
    ):
        session = acquire(manager)
        raise ValueError("synthetic task failure")
    assert session.context.closed
    assert factory.playwright.stop_calls == 1


def test_user_closed_page_reopens_at_bound_entry_without_disturbing_popup(manager, factory) -> None:
    session = acquire(manager)
    session.page.url = "https://careers.example.test/unapproved-transient-url"
    popup = session.context.new_page()
    popup.values["user_work"] = "keep me"
    session.page.close()
    reopened = acquire(manager)
    assert reopened is not session
    assert reopened.context is session.context
    assert reopened.page is not session.page
    assert reopened.page.navigations == [(DESTINATION, "domcontentloaded")]
    assert acquire(manager) is reopened
    assert not popup.closed
    assert popup.values == {"user_work": "keep me"}
    assert len(factory.playwright.launches) == 1


def test_user_closed_browser_relaunches_same_profile_and_binding(manager, factory) -> None:
    session = acquire(manager)
    session.context.user_close()
    reopened = acquire(manager)
    assert reopened.context is not session.context
    assert reopened.profile_path == session.profile_path
    assert reopened.binding == session.binding
    assert reopened.page.navigations == [(DESTINATION, "domcontentloaded")]
    assert factory.start_calls == 1
    assert len(factory.playwright.launches) == 2


def test_deferred_browser_close_event_is_dispatched_before_returning_cached_page(manager) -> None:
    session = acquire(manager)
    session.page.on_heartbeat = session.context.user_close
    reopened = acquire(manager)
    assert session.page.heartbeat_calls == [0]
    assert reopened.context is not session.context
    assert reopened.page.navigations == [(DESTINATION, "domcontentloaded")]


def test_deferred_browser_close_frees_capacity_for_another_application(tmp_path, factory) -> None:
    with PortalSessionManager(tmp_path / "profiles", 1, factory) as manager:
        session = acquire(manager)
        session.page.on_heartbeat = session.context.user_close
        other = acquire(manager, "other-application")
        assert session.context.closed
        assert not other.context.closed


def test_unavailable_live_page_fails_without_navigating_or_closing(manager) -> None:
    session = acquire(manager)
    session.page.heartbeat_error = RuntimeError("Synthetic transient protocol failure")
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager)
    assert failure.value.reason == "session_unavailable"
    assert session.page.navigations == [(DESTINATION, "domcontentloaded")]
    assert session.context.close_calls == 0
    session.page.heartbeat_error = None
    assert acquire(manager) is session


def test_binding_mismatch_does_not_even_pump_page_events(manager) -> None:
    session = acquire(manager)
    with pytest.raises(PortalSessionError):
        acquire(manager, answers_fingerprint="revised")
    assert session.page.heartbeat_calls == []


def test_closed_context_does_not_consume_capacity(tmp_path, factory) -> None:
    with PortalSessionManager(tmp_path / "profiles", 1, factory) as manager:
        first = acquire(manager)
        first.context.user_close()
        other = acquire(manager, "another-application")
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "session_limit"
        assert not other.context.closed
        manager.close_session(other.application_id, other.tracker)
        assert acquire(manager).context is not first.context


def test_closed_context_binding_still_requires_explicit_close(manager) -> None:
    session = acquire(manager)
    session.context.user_close()
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager, answers_fingerprint="changed")
    assert failure.value.reason == "binding_changed"
    manager.close_session(session.application_id, session.tracker)
    assert acquire(manager, answers_fingerprint="changed").binding.answers_fingerprint == "changed"


def test_tracks_use_distinct_profiles_and_keys(manager) -> None:
    test_session = acquire(manager, "same-application", tracker="test")
    production_session = acquire(manager, "same-application", tracker=TrackerName.PRODUCTION)
    assert test_session.context is not production_session.context
    assert test_session.profile_path.parent.name == "test"
    assert production_session.profile_path.parent.name == "production"
    assert acquire(manager, "same-application", tracker="production") is production_session


@pytest.mark.parametrize(
    "application_id", ["../../outside", "/absolute/path", "..", "a\\b", "x" * 5000]
)
def test_application_ids_cannot_escape_configured_root(tmp_path, factory, application_id) -> None:
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        session = acquire(manager, application_id)
        assert session.profile_path.is_relative_to(root.resolve() / "test")
        assert session.profile_path.parent == root.resolve() / "test"
        assert len(session.profile_path.name) <= 113
        assert session.profile_path.is_dir()


def test_sanitized_id_collisions_do_not_share_profiles(manager) -> None:
    assert acquire(manager, "a/b").profile_path != acquire(manager, "a?b").profile_path


@pytest.mark.parametrize("target", ["tracker", "profile", "lock", "hardlinked-lock"])
def test_profile_symlinks_and_hardlinked_locks_are_rejected(tmp_path, factory, target) -> None:
    root = tmp_path / "profiles"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_lock = outside / "untouched-lock"
    outside_lock.write_text("unchanged", encoding="utf-8")
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        session = acquire(manager)
        manager.close_session(session.application_id, session.tracker)
        if target == "tracker":
            tracker = session.profile_path.parent
            tracker.rename(root / "original-tracker")
            tracker.symlink_to(outside, target_is_directory=True)
        elif target == "profile":
            session.profile_path.rename(session.profile_path.with_name("original-profile"))
            session.profile_path.symlink_to(outside, target_is_directory=True)
        else:
            lock = session.profile_path / ".backend-scout-session.lock"
            lock.unlink()
            if target == "lock":
                lock.symlink_to(outside_lock)
            else:
                lock.hardlink_to(outside_lock)
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "unsafe_profile"
        assert outside_lock.read_text(encoding="utf-8") == "unchanged"
        assert list(outside.iterdir()) == [outside_lock]
        assert len(factory.playwright.launches) == 1


def test_profile_lock_blocks_second_owner_until_explicit_close(tmp_path, factory) -> None:
    root = tmp_path / "profiles"
    second_factory = FakeFactory()
    with (
        PortalSessionManager(root, playwright_factory=factory) as first,
        PortalSessionManager(root, playwright_factory=second_factory) as second,
    ):
        session = acquire(first)
        for _ in range(3):
            with pytest.raises(PortalSessionError) as failure:
                acquire(second)
            assert failure.value.reason == "profile_locked"
        assert second_factory.calls == 0
        assert first.close_session(session.application_id, session.tracker)
        assert acquire(second).profile_path == session.profile_path


def test_user_browser_close_releases_profile_lock(tmp_path, factory) -> None:
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as first:
        session = acquire(first)
        session.context.user_close()
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as second:
            assert acquire(second).profile_path == session.profile_path


@pytest.mark.parametrize("error", [RuntimeError("synthetic start failure"), KeyboardInterrupt()])
def test_start_failure_cleans_launcher_and_releases_lock(tmp_path, factory, error) -> None:
    factory.start_error = error
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else PortalSessionError
        with pytest.raises(expected):
            acquire(manager)
        assert factory.exit_calls == 1
        assert not factory.playwright.launches
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as other:
            acquire(other)
        factory.start_error = None
        acquire(manager)
        assert factory.start_calls == 2


def test_factory_failure_releases_lock_for_retry(tmp_path, factory) -> None:
    factory.factory_error = RuntimeError("synthetic factory failure")
    with PortalSessionManager(tmp_path / "profiles", playwright_factory=factory) as manager:
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "browser_start_failed"
        factory.factory_error = None
        acquire(manager)


def test_failed_startup_cleanup_requires_shutdown_and_can_be_retried(tmp_path, factory) -> None:
    manager = PortalSessionManager(tmp_path / "profiles", playwright_factory=factory)
    factory.start_error = RuntimeError("synthetic start failure")
    factory.exit_error = RuntimeError("synthetic cleanup failure")
    try:
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "browser_start_failed"
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "startup_cleanup_required"
        with pytest.raises(PortalSessionError, match="shutdown failed"):
            manager.close()
        factory.exit_error = None
        manager.close()
        assert factory.start_calls == 1
    finally:
        factory.exit_error = None
        manager.close()


def test_browser_launch_failure_stops_unused_driver_and_releases_lock(tmp_path, factory) -> None:
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        factory.playwright.launch_error = RuntimeError("synthetic browser launch failure")
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "session_open_failed"
        assert factory.playwright.stop_calls == 1
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as other:
            acquire(other)
        factory.playwright.launch_error = None
        acquire(manager)
        assert factory.start_calls == 2


def test_browser_launch_failure_does_not_stop_another_active_form(manager, factory) -> None:
    existing = acquire(manager)
    factory.playwright.launch_error = RuntimeError("synthetic browser launch failure")
    with pytest.raises(PortalSessionError):
        acquire(manager, "other-application")
    assert acquire(manager) is existing
    assert existing.context.close_calls == factory.playwright.stop_calls == 0


@pytest.mark.parametrize("stage", ["page_creation", "navigation"])
def test_initial_page_failure_closes_context_and_frees_profile(tmp_path, factory, stage) -> None:
    context = FakeContext(initial_page=stage != "page_creation")
    if stage == "page_creation":
        context.new_page_error = RuntimeError("synthetic page failure")
    else:
        context.pages[0].goto_error = RuntimeError("synthetic navigation failure")
    factory.playwright.pending_contexts.append(context)
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "session_open_failed"
        assert context.closed
        assert factory.playwright.stop_calls == 1
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as other:
            acquire(other)
        acquire(manager)


def test_failed_initial_navigation_and_close_keep_ownership_until_cleanup(
    tmp_path, factory
) -> None:
    context = FakeContext()
    context.pages[0].goto_error = RuntimeError("synthetic navigation failure")
    context.close_error = RuntimeError("synthetic close failure")
    factory.playwright.pending_contexts.append(context)
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "session_open_failed"
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "session_cleanup_required"
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as other:
            with pytest.raises(PortalSessionError) as failure:
                acquire(other)
            assert failure.value.reason == "profile_locked"
        context.close_error = None
        manager.close_session("application-123", "test")
        acquire(manager)


def test_reopen_failure_closes_only_replacement_page_and_allows_retry(manager) -> None:
    session = acquire(manager)
    popup = session.context.new_page()
    session.page.close()
    failed_page = FakePage()
    failed_page.goto_error = RuntimeError("synthetic navigation failure")
    session.context.new_pages.append(failed_page)
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager)
    assert failure.value.reason == "session_open_failed"
    assert failed_page.closed
    assert not popup.closed
    assert session.context.close_calls == 0
    assert acquire(manager).page is not failed_page


def test_reopen_cleanup_failure_requires_explicit_close(manager) -> None:
    session = acquire(manager)
    session.page.close()
    failed_page = FakePage()
    failed_page.goto_error = RuntimeError("synthetic navigation failure")
    failed_page.close_error = RuntimeError("synthetic page close failure")
    session.context.new_pages.append(failed_page)
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager)
    assert failure.value.reason == "session_open_failed"
    with pytest.raises(PortalSessionError) as failure:
        acquire(manager)
    assert failure.value.reason == "session_cleanup_required"
    assert session.context.close_calls == 0
    manager.close_session(session.application_id, session.tracker)
    acquire(manager)


def test_interrupted_page_reopen_closes_new_page_without_closing_existing_context(manager) -> None:
    session = acquire(manager)
    session.page.close()
    interrupted_page = FakePage()
    interrupted_page.goto_error = KeyboardInterrupt()
    session.context.new_pages.append(interrupted_page)
    with pytest.raises(KeyboardInterrupt):
        acquire(manager)
    assert interrupted_page.closed
    assert session.context.close_calls == 0
    assert acquire(manager).page is not interrupted_page


def test_context_closed_during_page_reopen_is_relaunched_at_approved_entry(manager) -> None:
    session = acquire(manager)
    session.page.close()

    def closed_during_new_page():
        session.context.user_close()
        raise RuntimeError("synthetic context closed")

    session.context.new_page = closed_during_new_page
    reopened = acquire(manager)
    assert reopened.context is not session.context
    assert reopened.page.navigations == [(DESTINATION, "domcontentloaded")]


def test_close_session_failure_keeps_lock_until_retry(tmp_path, factory) -> None:
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        session = acquire(manager)
        session.context.close_error = RuntimeError("synthetic close failure")
        with pytest.raises(PortalSessionError) as failure:
            manager.close_session(session.application_id, session.tracker)
        assert failure.value.reason == "session_close_failed"
        assert acquire(manager) is session
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as other:
            with pytest.raises(PortalSessionError) as failure:
                acquire(other)
            assert failure.value.reason == "profile_locked"
            session.context.close_error = None
            manager.close_session(session.application_id, session.tracker)
            acquire(other)


def test_shutdown_attempts_other_contexts_and_driver_after_close_error(manager, factory) -> None:
    first = acquire(manager)
    second = acquire(manager, "second")
    first.context.close_error = RuntimeError("synthetic close failure")
    with pytest.raises(PortalSessionError) as failure:
        manager.close()
    assert failure.value.reason == "shutdown_failed"
    assert second.context.close_calls == 1
    assert first.context.closed and second.context.closed
    assert factory.playwright.stop_calls == 1
    manager.close()
    assert factory.playwright.stop_calls == 1


def test_shutdown_failure_retains_unclosed_profile_and_retries(tmp_path, factory) -> None:
    root = tmp_path / "profiles"
    manager = PortalSessionManager(root, playwright_factory=factory)
    session = acquire(manager)
    session.context.close_error = RuntimeError("synthetic context close failure")
    factory.playwright.stop_error = RuntimeError("synthetic driver stop failure")
    try:
        with pytest.raises(PortalSessionError) as failure:
            manager.close()
        assert failure.value.reason == "shutdown_failed"
        with pytest.raises(PortalSessionError) as failure:
            acquire(manager)
        assert failure.value.reason == "manager_closed"
        with PortalSessionManager(root, playwright_factory=FakeFactory()) as other:
            with pytest.raises(PortalSessionError) as failure:
                acquire(other)
            assert failure.value.reason == "profile_locked"
            session.context.close_error = None
            factory.playwright.stop_error = None
            manager.close()
            acquire(other)
    finally:
        session.context.close_error = None
        factory.playwright.stop_error = None
        manager.close()


def test_context_manager_does_not_hide_original_error_when_shutdown_fails(
    tmp_path, factory
) -> None:
    manager = PortalSessionManager(tmp_path / "profiles", playwright_factory=factory)
    try:
        with pytest.raises(ValueError, match="original task error") as failure, manager:
            session = acquire(manager)
            session.context.close_error = RuntimeError("synthetic close failure")
            raise ValueError("original task error")
        assert "shutdown failed" in failure.value.__notes__[0]
    finally:
        manager.close()


@pytest.mark.parametrize("operation", ["acquire", "close_session", "close", "enter"])
def test_cross_thread_calls_fail_without_mutating_owner(manager, factory, operation) -> None:
    session = acquire(manager)
    failures = []

    def wrong_thread() -> None:
        try:
            if operation == "acquire":
                acquire(manager)
            elif operation == "close_session":
                manager.close_session(session.application_id, session.tracker)
            elif operation == "close":
                manager.close()
            else:
                manager.__enter__()
        except PortalSessionError as exc:
            failures.append(exc.reason)

    thread = Thread(target=wrong_thread)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert failures == ["wrong_thread"]
    assert acquire(manager) is session
    assert session.context.close_calls == factory.playwright.stop_calls == 0


@pytest.mark.parametrize(
    "invalid",
    [
        {"application_id": ""},
        {"application_id": "   "},
        {"application_id": "\ud800"},
        {"tracker": "../../other-tracker"},
        {"destination": "file:///outside"},
        {"destination": "javascript:alert(1)"},
        {"destination": "http://careers.example.test/apply"},
        {"destination": "https:///no-host"},
        {"destination": "https://username:password@careers.example.test/apply"},
        {"destination": "https://careers.example.test/apply\n"},
        {"destination": "https://careers.example.test/apply\\other"},
        {"destination": "https://careers.example.test:99999/apply"},
        {"destination": "https://careers.example.test:invalid/apply"},
        {"destination": ""},
        {"destination": "\ud800"},
        {"cv_sha256": "invalid"},
        {"cv_sha256": "zz" * 32},
        {"cv_sha256": None},
        {"answers_fingerprint": ""},
        {"answers_fingerprint": "\ud800"},
    ],
)
def test_invalid_binding_fails_before_filesystem_or_browser(tmp_path, factory, invalid) -> None:
    root = tmp_path / "profiles"
    with PortalSessionManager(root, playwright_factory=factory) as manager:
        with pytest.raises(ValueError):
            acquire(manager, **invalid)
        assert factory.calls == 0
        assert not root.exists()
