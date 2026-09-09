import base64
from email import message_from_bytes
from pathlib import Path

import keyring

from backend_scout.gmail import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SCOPES,
    GMAIL_SEND_SCOPE,
    list_recent_messages,
    send_email,
)


def test_gmail_send_uses_reviewed_recipient_body_and_attachment(
    monkeypatch, tmp_path: Path
) -> None:
    attachment = tmp_path / "approved.pdf"
    attachment.write_bytes(b"approved CV")
    captured: dict[str, object] = {}

    class FakeCredentials:
        expired = False
        refresh_token = None

    class FakeRequest:
        def execute(self) -> dict[str, str]:
            return {"id": "gmail-message-123"}

    class FakeMessages:
        def send(self, userId: str, body: dict[str, str]) -> FakeRequest:
            captured["user_id"] = userId
            captured["raw"] = body["raw"]
            return FakeRequest()

    class FakeUsers:
        def messages(self) -> FakeMessages:
            return FakeMessages()

    class FakeService:
        def users(self) -> FakeUsers:
            return FakeUsers()

    monkeypatch.setattr(keyring, "get_password", lambda *_: '{"token": "test"}')
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_info",
        lambda *_: FakeCredentials(),
    )
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *_args, **_kwargs: FakeService())

    message_id = send_email("jobs@example.com", "Reviewed subject", "Reviewed body", attachment)
    message = message_from_bytes(base64.urlsafe_b64decode(captured["raw"]))

    assert message_id == "gmail-message-123"
    assert captured["user_id"] == "me"
    assert message["To"] == "jobs@example.com"
    assert message["Subject"] == "Reviewed subject"
    assert message.get_payload()[0].get_payload(decode=True) == b"Reviewed body\n"
    assert message.get_payload()[1].get_filename() == "approved.pdf"


def test_gmail_scopes_include_send_and_readonly() -> None:
    assert GMAIL_SEND_SCOPE in GMAIL_SCOPES
    assert GMAIL_READONLY_SCOPE in GMAIL_SCOPES


def test_list_recent_messages_reads_safe_metadata(monkeypatch) -> None:
    class FakeCredentials:
        expired = False
        refresh_token = None

    class FakeListRequest:
        def execute(self) -> dict[str, object]:
            return {"messages": [{"id": "msg-1"}]}

    class FakeGetRequest:
        def execute(self) -> dict[str, object]:
            return {
                "id": "msg-1",
                "threadId": "thread-1",
                "snippet": "Thank you for applying.",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "jobs@example.com"},
                        {"name": "Subject", "value": "Application received"},
                        {"name": "Date", "value": "Wed, 9 Sep 2026 08:00:00 +0300"},
                    ]
                },
            }

    class FakeMessages:
        def list(self, userId: str, q: str, maxResults: int) -> FakeListRequest:
            assert userId == "me"
            assert q == "newer_than:7d"
            assert maxResults == 5
            return FakeListRequest()

        def get(self, userId: str, id: str, format: str, metadataHeaders: list) -> FakeGetRequest:
            assert userId == "me"
            assert id == "msg-1"
            assert format == "metadata"
            assert "Subject" in metadataHeaders
            return FakeGetRequest()

    class FakeUsers:
        def messages(self) -> FakeMessages:
            return FakeMessages()

    class FakeService:
        def users(self) -> FakeUsers:
            return FakeUsers()

    monkeypatch.setattr(keyring, "get_password", lambda *_: '{"token": "test"}')
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_info",
        lambda *_: FakeCredentials(),
    )
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *_args, **_kwargs: FakeService())

    messages = list_recent_messages("newer_than:7d", 5)

    assert len(messages) == 1
    assert messages[0].message_id == "msg-1"
    assert messages[0].subject == "Application received"
    assert messages[0].from_header == "jobs@example.com"
