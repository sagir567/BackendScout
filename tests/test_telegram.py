from backend_scout.models import ApplicationDigestItem, ApplicationStatus
from backend_scout.telegram import (
    TelegramApprovalAction,
    build_digest_reply_markup,
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

    action, page_id = parse_callback_data(payload)

    assert action == TelegramApprovalAction.APPROVE_TO_TAILOR
    assert page_id == "page-123"


def test_build_digest_reply_markup_contains_expected_actions() -> None:
    markup = build_digest_reply_markup("page-123")

    buttons = markup["inline_keyboard"][0]
    assert buttons[0]["text"] == "Approve tailoring"
    assert buttons[1]["text"] == "Close"


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
