import httpx
import pytest

from backend_scout.config import TrackerName
from backend_scout.models import ApplicationDigestItem, ApplicationStatus
from backend_scout.telegram import (
    TelegramApprovalAction,
    TelegramClient,
    build_cv_draft_reply_markup,
    build_digest_reply_markup,
    build_portal_submit_reply_markup,
    encode_callback_data,
    format_digest_message,
    parse_callback_data,
    process_telegram_update,
    send_digest_messages,
)


def test_format_digest_message_includes_key_job_fields() -> None:
    item = ApplicationDigestItem(
        notion_page_id="page-123",
        company="Example Cloud",
        title="Backend Engineer",
        status=ApplicationStatus.FOUND,
        source="manual",
        source_url="https://example.com/jobs/backend",
        location="Tel Aviv, Israel",
        remote_policy="Hybrid, 2 days from home",
        salary_text="18,000 NIS",
        match_score=87,
        match_reason="Recommendation: apply. Strong backend overlap.",
        required_skills=["Python", "FastAPI", "PostgreSQL"],
    )

    message = format_digest_message(item)

    assert "Example Cloud - Backend Engineer" in message
    assert "Score: 87" in message
    assert "Skills: Python, FastAPI, PostgreSQL" in message
    assert "Source: https://example.com/jobs/backend" in message


def test_parse_callback_data_round_trips() -> None:
    payload = encode_callback_data(TelegramApprovalAction.APPROVE_TO_TAILOR, "page-123")

    action, page_id, draft_id, tracker = parse_callback_data(payload)

    assert action == TelegramApprovalAction.APPROVE_TO_TAILOR
    assert page_id == "page-123"
    assert draft_id is None
    assert tracker == TrackerName.TEST


def test_production_callback_cannot_be_processed_by_the_test_tracker() -> None:
    payload = encode_callback_data(
        TelegramApprovalAction.APPROVE_TO_TAILOR,
        "page-123",
        tracker=TrackerName.PRODUCTION,
    )
    update = {"callback_query": {"from": {"id": 12345}, "data": payload}}

    class FakeTelegramClient:
        pass

    class FakeNotionClient:
        def retrieve_page(self, page_id: str) -> dict[str, object]:
            raise AssertionError("the wrong tracker must be rejected before reading the page")

    with pytest.raises(ValueError, match="different tracker"):
        process_telegram_update(FakeTelegramClient(), FakeNotionClient(), update, {12345})


def test_build_digest_reply_markup_contains_expected_actions() -> None:
    markup = build_digest_reply_markup("page-123")

    buttons = markup["inline_keyboard"][0]
    assert buttons[0]["text"] == "Approve tailoring"
    assert buttons[1]["text"] == "Close"


def test_build_cv_draft_reply_markup_binds_approval_to_draft_id() -> None:
    markup = build_cv_draft_reply_markup("page-123", "deadbeef")

    approve_button = markup["inline_keyboard"][0][0]
    assert approve_button["text"] == "Approve this CV"
    assert approve_button["callback_data"] == "s:t:page-123:deadbeef"


def test_build_portal_submit_markup_binds_the_final_action_to_one_request() -> None:
    markup = build_portal_submit_reply_markup("page-123", "approval-1", TrackerName.PRODUCTION)

    submit_button = markup["inline_keyboard"][0][0]
    assert submit_button["text"] == "Submit now"
    assert submit_button["callback_data"] == "p:p:page-123:approval-1"


def test_process_telegram_update_authorizes_portal_submit_without_changing_status() -> None:
    authorized: list[tuple[str, str]] = []
    updated_statuses: list[tuple[str, ApplicationStatus]] = []

    class FakeTelegramClient:
        def answer_callback_query(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

        def edit_message_reply_markup(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

        def send_message(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

    class FakeNotionClient:
        def retrieve_page(self, page_id: str) -> dict[str, object]:
            return {
                "id": page_id,
                "properties": {
                    "Role": {"title": [{"plain_text": "Backend Engineer"}]},
                    "Company": {"rich_text": [{"plain_text": "Example Cloud"}]},
                    "Status": {"status": {"name": "submission_prepared"}},
                    "Source": {"rich_text": [{"plain_text": "manual"}]},
                    "Source URL": {"url": "https://example.test/jobs/backend"},
                    "Location": {"rich_text": []},
                    "Remote Policy": {"rich_text": []},
                    "Employment Type": {"rich_text": []},
                    "Salary": {"rich_text": []},
                    "Match Score": {"number": 87},
                    "Required Skills": {"multi_select": []},
                    "Years Experience": {"rich_text": []},
                    "Match Reason": {"rich_text": []},
                    "Description": {"rich_text": [{"plain_text": "Build APIs."}]},
                    "Discovered At": {"date": {"start": "2026-09-01"}},
                },
            }

        def update_application_status(self, page_id: str, status: ApplicationStatus) -> dict[str, object]:
            updated_statuses.append((page_id, status))
            return {"id": page_id}

    result = process_telegram_update(
        FakeTelegramClient(),
        FakeNotionClient(),
        {
            "callback_query": {
                "id": "callback-portal",
                "from": {"id": 12345},
                "data": "p:t:page-123:approval-1",
                "message": {"message_id": 99, "chat": {"id": 12345}},
            }
        },
        {12345},
        portal_submission_authorization_handler=lambda page_id, authorization_id: authorized.append(
            (page_id, authorization_id)
        ),
    )

    assert result == "submission_prepared"
    assert authorized == [("page-123", "approval-1")]
    assert updated_statuses == []


def test_send_document_uses_multipart_form_data(tmp_path) -> None:
    received_content_type = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_content_type
        received_content_type = request.headers["content-type"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    document_path = tmp_path / "cv_draft.pdf"
    document_path.write_bytes(b"test pdf")
    client = TelegramClient("test-token")
    client._client.close()
    client._client = httpx.Client(
        base_url="https://example.test/bottest-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.send_document(12345, document_path, "Review this draft")
    finally:
        client.close()

    assert received_content_type.startswith("multipart/form-data;")
    assert result["result"]["message_id"] == 1


def test_send_photo_uses_multipart_form_data(tmp_path) -> None:
    received_content_type = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_content_type
        received_content_type = request.headers["content-type"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    screenshot_path = tmp_path / "proof.png"
    screenshot_path.write_bytes(b"test png")
    client = TelegramClient("test-token")
    client._client.close()
    client._client = httpx.Client(
        base_url="https://example.test/bottest-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.send_photo(12345, screenshot_path, "Submission proof")
    finally:
        client.close()

    assert received_content_type.startswith("multipart/form-data;")
    assert result["result"]["message_id"] == 1


def test_send_digest_messages_sends_buttons_and_advances_found_job() -> None:
    sent_messages = []
    updated_statuses = []

    class FakeTelegramClient:
        def send_message(self, chat_id: int, text: str, reply_markup=None) -> dict[str, object]:
            sent_messages.append((chat_id, text, reply_markup))
            return {"message_id": 77}

    class FakeNotionClient:
        def update_application_status(
            self, page_id: str, status: ApplicationStatus
        ) -> dict[str, object]:
            updated_statuses.append((page_id, status))
            return {"id": page_id}

    item = ApplicationDigestItem(
        notion_page_id="page-123",
        company="Example Cloud",
        title="Backend Engineer",
        status=ApplicationStatus.FOUND,
        source="manual",
        source_url="https://example.com/jobs/backend",
        match_score=87,
    )

    result = send_digest_messages(FakeTelegramClient(), FakeNotionClient(), 12345, [item])

    assert result == [{"message_id": 77}]
    assert sent_messages[0][0] == 12345
    assert "Example Cloud - Backend Engineer" in sent_messages[0][1]
    assert sent_messages[0][2]["inline_keyboard"][0][0]["text"] == "Approve tailoring"
    assert updated_statuses == [("page-123", ApplicationStatus.DIGEST_SENT)]


def test_process_telegram_update_approves_tailoring_for_allowed_user() -> None:
    answered_callbacks = []
    sent_messages = []
    edited_messages = []
    updated_statuses = []

    class FakeTelegramClient:
        def answer_callback_query(self, callback_query_id: str, text: str) -> dict[str, object]:
            answered_callbacks.append((callback_query_id, text))
            return {"ok": True}

        def edit_message_reply_markup(
            self,
            chat_id: int,
            message_id: int,
            reply_markup: dict[str, object] | None = None,
        ) -> dict[str, object]:
            edited_messages.append((chat_id, message_id, reply_markup))
            return {"ok": True}

        def send_message(self, chat_id: int, text: str, reply_markup=None) -> dict[str, object]:
            sent_messages.append((chat_id, text, reply_markup))
            return {"ok": True}

    class FakeNotionClient:
        def retrieve_page(self, page_id: str) -> dict[str, object]:
            return {
                "id": page_id,
                "properties": {
                    "Role": {"title": [{"plain_text": "Backend Engineer"}]},
                    "Company": {"rich_text": [{"plain_text": "Example Cloud"}]},
                    "Status": {"status": {"name": "digest_sent"}},
                    "Source": {"rich_text": [{"plain_text": "manual"}]},
                    "Source URL": {"url": "https://example.com/jobs/backend"},
                    "Location": {"rich_text": [{"plain_text": "Tel Aviv, Israel"}]},
                    "Remote Policy": {"rich_text": [{"plain_text": "Hybrid"}]},
                    "Employment Type": {"rich_text": []},
                    "Salary": {"rich_text": []},
                    "Match Score": {"number": 87},
                    "Required Skills": {"multi_select": [{"name": "Python"}]},
                    "Years Experience": {"rich_text": []},
                    "Match Reason": {"rich_text": [{"plain_text": "Recommendation: apply."}]},
                    "Description": {"rich_text": [{"plain_text": "Build APIs."}]},
                    "Discovered At": {"date": {"start": "2026-08-31"}},
                },
            }

        def update_application_status(self, page_id: str, status: ApplicationStatus) -> dict[str, object]:
            updated_statuses.append((page_id, status))
            return {"id": page_id}

    update = {
        "update_id": 11,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345},
            "data": "approve_to_tailor:page-123",
            "message": {
                "message_id": 99,
                "chat": {"id": 12345},
            },
        },
    }

    result = process_telegram_update(
        FakeTelegramClient(),
        FakeNotionClient(),
        update,
        {12345},
    )

    assert result == "approved_to_tailor"
    assert updated_statuses == [("page-123", ApplicationStatus.APPROVED_TO_TAILOR)]
    assert answered_callbacks
    assert edited_messages
    assert sent_messages


def test_process_telegram_update_acknowledges_callback_before_notion_work() -> None:
    events: list[str] = []

    class FakeTelegramClient:
        def answer_callback_query(self, callback_query_id: str, text: str) -> dict[str, object]:
            events.append(f"ack:{text}")
            return {"ok": True}

        def edit_message_reply_markup(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

        def send_message(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

    class FakeNotionClient:
        def retrieve_page(self, page_id: str) -> dict[str, object]:
            events.append("retrieve_page")
            return {
                "id": page_id,
                "properties": {
                    "Role": {"title": [{"plain_text": "Backend Engineer"}]},
                    "Company": {"rich_text": [{"plain_text": "Example Cloud"}]},
                    "Status": {"status": {"name": "digest_sent"}},
                    "Source": {"rich_text": [{"plain_text": "manual"}]},
                    "Source URL": {"url": "https://example.com/jobs/backend"},
                    "Location": {"rich_text": []},
                    "Remote Policy": {"rich_text": []},
                    "Employment Type": {"rich_text": []},
                    "Salary": {"rich_text": []},
                    "Match Score": {"number": 87},
                    "Required Skills": {"multi_select": []},
                    "Years Experience": {"rich_text": []},
                    "Match Reason": {"rich_text": []},
                    "Description": {"rich_text": [{"plain_text": "Build APIs."}]},
                    "Discovered At": {"date": {"start": "2026-09-01"}},
                },
            }

        def update_application_status(self, page_id: str, status: ApplicationStatus) -> dict[str, object]:
            events.append(f"status:{status.value}")
            return {"id": page_id}

    process_telegram_update(
        FakeTelegramClient(),
        FakeNotionClient(),
        {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 12345},
                "data": "approve_to_tailor:page-123",
                "message": {"message_id": 99, "chat": {"id": 12345}},
            }
        },
        {12345},
    )

    assert events[:2] == ["ack:Received. Processing...", "retrieve_page"]


def test_process_telegram_update_ignores_unapproved_user() -> None:
    class FakeTelegramClient:
        def answer_callback_query(self, callback_query_id: str, text: str) -> dict[str, object]:
            raise AssertionError("should not be called")

    class FakeNotionClient:
        def retrieve_page(self, page_id: str) -> dict[str, object]:
            raise AssertionError("should not be called")

    update = {
        "update_id": 12,
        "callback_query": {
            "id": "callback-2",
            "from": {"id": 999},
            "data": "approve_to_tailor:page-123",
        },
    }

    result = process_telegram_update(
        FakeTelegramClient(),
        FakeNotionClient(),
        update,
        {12345},
    )

    assert result is None


def test_process_telegram_update_keeps_approval_when_callback_acknowledgement_expires() -> None:
    updated_statuses = []

    class FakeTelegramClient:
        def answer_callback_query(self, callback_query_id: str, text: str) -> dict[str, object]:
            raise ValueError("query is too old")

        def edit_message_reply_markup(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

        def send_message(self, *args, **kwargs) -> dict[str, object]:
            return {"ok": True}

    class FakeNotionClient:
        def retrieve_page(self, page_id: str) -> dict[str, object]:
            return {
                "id": page_id,
                "properties": {
                    "Role": {"title": [{"plain_text": "Backend Engineer"}]},
                    "Company": {"rich_text": [{"plain_text": "Example Cloud"}]},
                    "Status": {"status": {"name": "digest_sent"}},
                    "Source": {"rich_text": [{"plain_text": "manual"}]},
                    "Source URL": {"url": "https://example.com/jobs/backend"},
                    "Location": {"rich_text": []},
                    "Remote Policy": {"rich_text": []},
                    "Employment Type": {"rich_text": []},
                    "Salary": {"rich_text": []},
                    "Match Score": {"number": 87},
                    "Required Skills": {"multi_select": []},
                    "Years Experience": {"rich_text": []},
                    "Match Reason": {"rich_text": []},
                    "Description": {"rich_text": [{"plain_text": "Build APIs."}]},
                    "Discovered At": {"date": {"start": "2026-09-01"}},
                },
            }

        def update_application_status(self, page_id: str, status: ApplicationStatus) -> dict[str, object]:
            updated_statuses.append((page_id, status))
            return {"id": page_id}

    update = {
        "update_id": 13,
        "callback_query": {
            "id": "expired-callback",
            "from": {"id": 12345},
            "data": "approve_to_tailor:page-123",
            "message": {"message_id": 99, "chat": {"id": 12345}},
        },
    }

    result = process_telegram_update(FakeTelegramClient(), FakeNotionClient(), update, {12345})

    assert result == "approved_to_tailor"
    assert updated_statuses == [("page-123", ApplicationStatus.APPROVED_TO_TAILOR)]


def test_process_telegram_message_sends_status_counts() -> None:
    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, chat_id: int, text: str, reply_markup=None) -> dict[str, object]:
            sent_messages.append((chat_id, text))
            return {"ok": True}

    class FakeNotionClient:
        def query_data_source(self, data_source_id: str, payload: dict[str, object]) -> dict[str, object]:
            return {
                "results": [
                    _application_page("page-1", ApplicationStatus.SUBMITTED),
                    _application_page("page-2", ApplicationStatus.INTERVIEW),
                ]
            }

    result = process_telegram_update(
        FakeTelegramClient(),
        FakeNotionClient(),
        {"message": {"from": {"id": 12345}, "chat": {"id": 12345}, "text": "/status"}},
        {12345},
        data_source_id="data-source-123",
    )

    assert result == "status_sent"
    assert sent_messages[0][0] == 12345
    assert "interview: 1" in sent_messages[0][1]
    assert "submitted: 1" in sent_messages[0][1]


def test_process_telegram_message_queues_today_scout(monkeypatch: pytest.MonkeyPatch) -> None:
    queued = []
    sent_messages = []

    class FakeTask:
        task_id = "task-123"

    class FakeTelegramClient:
        def send_message(self, chat_id: int, text: str, reply_markup=None) -> dict[str, object]:
            sent_messages.append((chat_id, text))
            return {"ok": True}

    class FakeNotionClient:
        pass

    monkeypatch.setattr(
        "backend_scout.telegram.enqueue_task",
        lambda kind, tracker, payload: queued.append((kind, tracker, payload)) or FakeTask(),
    )

    result = process_telegram_update(
        FakeTelegramClient(),
        FakeNotionClient(),
        {"message": {"from": {"id": 12345}, "chat": {"id": 12345}, "text": "/scout"}},
        {12345},
        tracker=TrackerName.PRODUCTION,
    )

    assert result == "scout_queued"
    assert queued[0][1] == TrackerName.PRODUCTION
    assert queued[0][2] == {"chat_id": 12345}
    assert "task-123" in sent_messages[0][1]


def _application_page(page_id: str, status: ApplicationStatus) -> dict[str, object]:
    return {
        "id": page_id,
        "properties": {
            "Role": {"title": [{"plain_text": "Backend Engineer"}]},
            "Company": {"rich_text": [{"plain_text": "Example Cloud"}]},
            "Status": {"status": {"name": status.value}},
            "Source": {"rich_text": [{"plain_text": "manual"}]},
            "Source URL": {"url": "https://example.com/jobs/backend"},
            "Location": {"rich_text": []},
            "Remote Policy": {"rich_text": []},
            "Employment Type": {"rich_text": []},
            "Salary": {"rich_text": []},
            "Match Score": {"number": 87},
            "Required Skills": {"multi_select": []},
            "Years Experience": {"rich_text": []},
            "Match Reason": {"rich_text": []},
            "Description": {"rich_text": [{"plain_text": "Build APIs."}]},
            "Discovered At": {"date": {"start": "2026-09-01"}},
        },
    }
