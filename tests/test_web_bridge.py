"""Synthetic safety checks between legacy actions and the private review ledger."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from backend_scout import cli
from backend_scout import telegram as telegram_module
from backend_scout.application_service import OperationOutcome
from backend_scout.application_store import is_ledger_owned
from backend_scout.config import TrackerName
from backend_scout.cv_artifacts import write_manifest
from backend_scout.durable_runtime import ensure_application_sync
from backend_scout.task_queue import (
    QueuedTaskKind,
    QueuedTaskStatus,
    claim_next_task,
    enqueue_task,
    list_tasks,
)
from backend_scout.telegram import (
    BrowserPairingMessage,
    TelegramApprovalAction,
    encode_callback_data,
    process_telegram_update,
)

OWNER = 12345
TRACKER = TrackerName.TEST
PAGE_ID = "a3b71273-fa0b-4162-b5b0-e1d289d34670"
PAGE_VARIANTS = (
    PAGE_ID,
    PAGE_ID.upper(),
    PAGE_ID.replace("-", ""),
    PAGE_ID.replace("-", "").upper(),
)


@pytest.fixture(autouse=True)
def isolated_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def no_network(*args, **kwargs):
        pytest.fail("Bridge tests must never open a network connection")

    monkeypatch.setattr("socket.socket.connect", no_network)


def _message(text, *, sender=OWNER, chat_id=OWNER, chat_type="private"):
    return {
        "message": {
            "from": {"id": sender},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        }
    }


def _page():
    return {
        "id": PAGE_ID,
        "parent": {"data_source_id": "synthetic-test-tracker"},
        "properties": {
            "Role": {"title": [{"plain_text": "Backend Engineer"}]},
            "Company": {"rich_text": [{"plain_text": "Example"}]},
            "Status": {"status": {"name": "approved_to_submit"}},
            "Source": {"rich_text": [{"plain_text": "fixture"}]},
            "Source URL": {"url": "https://example.test/jobs/1"},
            "Application URL": {"url": "https://jobs.example.test/jobs/1/apply"},
        },
    }


@pytest.fixture
def bridge():
    events = []
    telegram = Mock(spec=["send_message", "answer_callback_query", "edit_message_reply_markup"])
    telegram.answer_callback_query.side_effect = lambda *args: events.append("ack")
    notion = Mock(spec=["retrieve_page", "update_application_status", "query_data_source"])
    notion.retrieve_page.return_value = _page()
    return SimpleNamespace(telegram=telegram, notion=notion, events=events, enqueue=Mock())


def _import_uuid(service, application_id):
    job = service.store.get_application("example-job", TRACKER).job
    service.store.import_snapshot(application_id, TRACKER, job, "cv_drafted")
    directory = service.settings.cv_archive_root / job.company / application_id / "v1"
    directory.mkdir(parents=True)
    docx, pdf = directory / "Synthetic CV.docx", directory / "Synthetic CV.pdf"
    docx.write_bytes(b"Synthetic DOCX fixture")
    pdf.write_bytes(b"%PDF-1.4\nSynthetic bridge fixture")
    write_manifest(directory, application_id, job.company, job.title, docx, pdf, TRACKER.value)


def _approve(service, application_id):
    package, _ = service.preview_package(application_id, TRACKER)
    return service.approve_and_queue(application_id, TRACKER, str(OWNER), package.fingerprint())


@pytest.fixture
def owned_review_service(review_service):
    _import_uuid(review_service, PAGE_ID)
    _approve(review_service, PAGE_ID)
    assert is_ledger_owned(review_service.store.database_path, PAGE_ID, TRACKER)
    return review_service


@pytest.mark.parametrize("command", ["/web", "/WEB", "/app"])
def test_private_owner_pairing_issues_once_and_sends_only_to_owner(bridge, command):
    pairing = BrowserPairingMessage("Open the private workspace", "synthetic-single-use-code")
    issue_code = Mock(return_value=pairing)
    guard = Mock(side_effect=AssertionError("Pairing is not an application mutation"))
    result = process_telegram_update(
        bridge.telegram,
        bridge.notion,
        _message(command),
        {OWNER},
        browser_pairing_handler=issue_code,
        application_action_guard=guard,
        task_enqueue_handler=bridge.enqueue,
    )
    assert result == "web_pairing_sent"
    issue_code.assert_called_once_with(OWNER)
    assert bridge.telegram.send_message.call_count == 2
    assert bridge.telegram.send_message.call_args_list[0].args == (OWNER, pairing.instructions)
    assert bridge.telegram.send_message.call_args_list[1].args == (OWNER, pairing.code)
    assert bridge.telegram.send_message.call_args_list[1].kwargs["reply_markup"] == {
        "inline_keyboard": [[{
            "text": "Copy sign-in code", "copy_text": {"text": pairing.code},
        }]],
    }
    assert pairing.code not in pairing.instructions
    assert pairing.code not in repr(pairing)
    guard.assert_not_called()
    assert not bridge.notion.mock_calls
    bridge.enqueue.assert_not_called()


def test_phone_snapshot_refresh_is_deduplicated_within_five_minute_bucket(tmp_path):
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "workflow.sqlite3"
    current = datetime(2026, 11, 2, 8, tzinfo=UTC)
    first = ensure_application_sync(path, current)
    assert ensure_application_sync(path, current + timedelta(seconds=15)) == first
    assert ensure_application_sync(path, current + timedelta(minutes=5)) != first


def test_cli_web_pair_sends_code_only_in_second_message(review_service, monkeypatch):
    from unittest.mock import MagicMock

    review_service.settings.telegram_bot_token = "synthetic-token"
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(cli, "Settings", lambda: review_service.settings)
    monkeypatch.setattr(cli, "TelegramClient", lambda _: client)
    result = CliRunner().invoke(cli.app, ["web", "pair"])
    assert result.exit_code == 0, result.output
    assert client.send_message.call_count == 2
    instructions, code = [call.args[1] for call in client.send_message.call_args_list]
    assert len(code) == 43 and "\n" not in code and " " not in code
    assert code not in instructions and code not in result.output
    assert "http://127.0.0.1:8765" in instructions


@pytest.mark.parametrize(
    ("sender", "chat_id", "chat_type"),
    [(67890, 67890, "private"), (OWNER, -100123, "group"), (OWNER, -100456, "supergroup")],
)
def test_unauthorized_or_group_pairing_never_calls_code_issuer(bridge, sender, chat_id, chat_type):
    issue_code = Mock(side_effect=AssertionError("Pairing code must not be issued"))
    result = process_telegram_update(
        bridge.telegram,
        bridge.notion,
        _message("/web", sender=sender, chat_id=chat_id, chat_type=chat_type),
        {OWNER},
        browser_pairing_handler=issue_code,
        task_enqueue_handler=bridge.enqueue,
    )
    issue_code.assert_not_called()
    bridge.enqueue.assert_not_called()
    assert not bridge.notion.mock_calls
    if sender != OWNER:
        assert result is None
        bridge.telegram.send_message.assert_not_called()
    else:
        assert result == "web_pairing_sent"
        assert "private chat" in bridge.telegram.send_message.call_args.args[1]


@pytest.mark.parametrize("action", list(TelegramApprovalAction))
def test_callback_acknowledged_then_guarded_before_any_notion_access_or_enqueue(
    bridge, owned_review_service, action
):
    def guard(page_id, tracker):
        assert bridge.events == ["ack"], "Callback must be acknowledged before the ownership guard"
        bridge.events.append("guard")
        cli._legacy_action_guard(owned_review_service.settings, page_id, tracker)

    update = {
        "callback_query": {
            "id": "synthetic-callback",
            "from": {"id": OWNER},
            "data": encode_callback_data(action, PAGE_ID, "synthetic-draft", TRACKER),
            "message": {"message_id": 99, "chat": {"id": OWNER, "type": "private"}},
        }
    }
    with pytest.raises(ValueError, match="private web review"):
        process_telegram_update(
            bridge.telegram,
            bridge.notion,
            update,
            {OWNER},
            tracker=TRACKER,
            data_source_id="synthetic-test-tracker",
            application_action_guard=guard,
            task_enqueue_handler=bridge.enqueue,
        )
    assert bridge.events == ["ack", "guard"]
    bridge.telegram.answer_callback_query.assert_called_once_with(
        "synthetic-callback", "Received. Processing..."
    )
    assert not bridge.notion.mock_calls, "Owner guard must reject before any Notion read or write"
    bridge.enqueue.assert_not_called()
    bridge.telegram.edit_message_reply_markup.assert_not_called()
    bridge.telegram.send_message.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        f"/draft_{PAGE_ID}",
        f"/prepare_{PAGE_ID}",
        f"/revise_{PAGE_ID} Clarify scope",
        f"/tailor_{PAGE_ID} Focus on tests",
    ],
)
def test_mutating_message_commands_refuse_before_reads_writes_or_enqueue(
    bridge, owned_review_service, monkeypatch, command
):
    writes = [Mock(), Mock()]
    monkeypatch.setattr(telegram_module, "save_revision_feedback", writes[0])
    monkeypatch.setattr(telegram_module, "save_tailoring_note", writes[1])
    guard = Mock(
        side_effect=lambda page_id, tracker: cli._legacy_action_guard(
            owned_review_service.settings, page_id, tracker
        )
    )
    with pytest.raises(ValueError, match="private web review"):
        process_telegram_update(
            bridge.telegram,
            bridge.notion,
            _message(command),
            {OWNER},
            tracker=TRACKER,
            application_action_guard=guard,
            task_enqueue_handler=bridge.enqueue,
        )
    guard.assert_called_once_with(PAGE_ID, TRACKER)
    assert not bridge.notion.mock_calls
    bridge.enqueue.assert_not_called()
    bridge.telegram.send_message.assert_not_called()
    for write in writes:
        write.assert_not_called()


def test_readonly_open_remains_available_for_ledger_owned_application(bridge, owned_review_service):
    page_before = deepcopy(bridge.notion.retrieve_page.return_value)
    guard = Mock(side_effect=AssertionError("Read-only open must not invoke the mutation guard"))
    result = process_telegram_update(
        bridge.telegram,
        bridge.notion,
        _message(f"/open_{PAGE_ID}"),
        {OWNER},
        tracker=TRACKER,
        data_source_id="synthetic-test-tracker",
        application_action_guard=guard,
        task_enqueue_handler=bridge.enqueue,
    )
    assert result == "application_link_sent"
    guard.assert_not_called()
    bridge.notion.retrieve_page.assert_called_once_with(PAGE_ID)
    bridge.notion.update_application_status.assert_not_called()
    bridge.enqueue.assert_not_called()
    assert bridge.notion.retrieve_page.return_value == page_before
    sent = bridge.telegram.send_message.call_args
    assert sent.args[0] == OWNER
    assert "does not submit anything" in sent.args[1]
    assert sent.kwargs["reply_markup"]["inline_keyboard"][0][0]["url"] == (
        "https://jobs.example.test/jobs/1/apply"
    )
    assert owned_review_service.store.get_application(PAGE_ID, TRACKER).status == "approved"


@pytest.mark.parametrize("stored_id", [PAGE_ID, PAGE_ID.replace("-", "").upper()])
def test_ledger_ownership_normalizes_uuid_case_and_hyphens_only_after_local_approval(
    review_service, stored_id
):
    _import_uuid(review_service, stored_id)
    path = review_service.store.database_path
    for query in PAGE_VARIANTS:
        assert not is_ledger_owned(path, query, TRACKER)
    review_service.preview_package(stored_id, TRACKER)
    assert not is_ledger_owned(path, stored_id, TRACKER)
    _approve(review_service, stored_id)
    for query in PAGE_VARIANTS:
        assert is_ledger_owned(path, query, TRACKER)
        assert not is_ledger_owned(path, query, TrackerName.PRODUCTION)
    assert not is_ledger_owned(path, "0" * 32, TRACKER)


@pytest.mark.parametrize(
    "command",
    [("apply", "prepare"), ("apply", "request-submit"), ("apply", "resume"), ("cv", "draft")],
)
@pytest.mark.parametrize("page_id", PAGE_VARIANTS)
def test_cli_legacy_mutations_refuse_owned_application_before_external_reads(
    owned_review_service, monkeypatch, command, page_id
):
    settings = Mock(return_value=owned_review_service.settings)
    monkeypatch.setattr(cli, "Settings", settings)
    external = {}
    for name in (
        "NotionClient",
        "TelegramClient",
        "load_career_evidence",
        "load_cv_style",
        "load_manifest",
        "load_browser_checkpoint",
        "load_application_form_answers",
        "prepare_visible_submission",
        "create_portal_submit_authorization",
    ):
        external[name] = Mock(
            side_effect=AssertionError("Legacy action reached an external dependency")
        )
        monkeypatch.setattr(cli, name, external[name])
    result = CliRunner().invoke(cli.app, [*command, page_id, "--tracker", TRACKER.value])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError), result.output
    assert "private web review" in str(result.exception)
    settings.assert_called_once_with()
    for dependency in external.values():
        dependency.assert_not_called()


def test_package_worker_records_typed_blocked_outcome_not_done(review_service, monkeypatch):
    path = review_service.store.database_path
    queued = enqueue_task(
        QueuedTaskKind.PACKAGE_CV_DRAFT, TRACKER, {"page_id": "example-job"}, path
    )
    claimed = claim_next_task(path)
    assert claimed.task_id == queued.task_id
    assert claimed.status == QueuedTaskStatus.RUNNING
    outcome = OperationOutcome(
        status="blocked",
        reason_code="full_posting_required",
        message="Synthetic missing requirements",
        correlation_id="synthetic-correlation",
        application_id="example-job",
    )
    service = Mock(spec=["draft"])
    service.draft.return_value = outcome
    factory = Mock(return_value=service)
    monkeypatch.setattr(cli, "Settings", lambda: review_service.settings)
    monkeypatch.setattr(cli, "build_application_service", factory)
    external = []
    for name in (
        "cv_draft",
        "apply_prepare",
        "apply_resume",
        "run_agent_chat",
    ):
        forbidden = Mock(
            side_effect=AssertionError("Typed package worker must not run a legacy action")
        )
        monkeypatch.setattr(cli, name, forbidden)
        external.append(forbidden)
    notification = Mock()
    monkeypatch.setattr(cli, "_send_task_telegram_message", notification)
    cli._process_claimed_task(claimed, path)
    notification.assert_called_once()
    assert "Synthetic missing requirements" in notification.call_args.args[1]
    service.draft.assert_called_once_with("example-job", TRACKER)
    factory.assert_called_once()
    assert factory.call_args.args[0].workflow_database_path == path
    persisted = next(task for task in list_tasks(path) if task.task_id == queued.task_id)
    assert persisted.status == QueuedTaskStatus.BLOCKED
    assert persisted.last_error == outcome.message
    assert persisted.lease_until is None
    for dependency in external:
        dependency.assert_not_called()
