import base64
from email import message_from_bytes
from pathlib import Path

import keyring

from backend_scout.gmail import send_email


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
