import base64
import builtins
import json
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import keyring
import pytest

from backend_scout import gmail
from backend_scout.gmail import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SCOPES,
    GMAIL_SEND_SCOPE,
    KEYRING_ACCOUNT,
    KEYRING_SERVICE,
    connect_gmail,
    gmail_connected,
    gmail_reauthorization_required,
    list_recent_messages,
    send_email,
)
from backend_scout.secrets import CredentialStoreError, FileCredentialStore


@pytest.fixture(autouse=True)
def clean_gmail_environment(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GMAIL_CREDENTIAL_STORE", raising=False)
    monkeypatch.delenv("GMAIL_CREDENTIAL_FILE", raising=False)


@pytest.fixture
def file_store(monkeypatch, tmp_path: Path):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "keyring" or name.startswith("keyring."):
            pytest.fail("File-backed Gmail must not import keyring")
        return original_import(name, *args, **kwargs)

    def forbidden_keyring(*_args, **_kwargs):
        pytest.fail("File-backed Gmail must not call keyring")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(keyring, "get_password", forbidden_keyring)
    monkeypatch.setattr(keyring, "set_password", forbidden_keyring)
    path = tmp_path / "private" / "gmail.json"
    monkeypatch.setenv("GMAIL_CREDENTIAL_STORE", "file")
    monkeypatch.setenv("GMAIL_CREDENTIAL_FILE", str(path))
    return FileCredentialStore(path)


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


def test_gmail_reauthorization_required_detects_revoked_refresh_token() -> None:
    error = RuntimeError("invalid_grant: Token has been expired or revoked.")

    assert gmail_reauthorization_required(error)
    assert not gmail_reauthorization_required(RuntimeError("network timeout"))


def test_list_recent_messages_reads_safe_metadata(monkeypatch) -> None:
    html_body = base64.urlsafe_b64encode(b"<p>Thank you for applying.</p>").decode("ascii")
    text_body = base64.urlsafe_b64encode(b"Thank you for applying.").decode("ascii")

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
                    ],
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": text_body}},
                        {"mimeType": "text/html", "body": {"data": html_body}},
                    ],
                },
            }

    class FakeMessages:
        def list(self, userId: str, q: str, maxResults: int) -> FakeListRequest:
            assert userId == "me"
            assert q == "newer_than:7d"
            assert maxResults == 5
            return FakeListRequest()

        def get(self, userId: str, id: str, format: str) -> FakeGetRequest:
            assert userId == "me"
            assert id == "msg-1"
            assert format == "full"
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
    assert messages[0].body_text == "Thank you for applying."
    assert messages[0].body_html == "<p>Thank you for applying.</p>"


def test_connect_gmail_still_saves_to_default_keyring(monkeypatch, tmp_path: Path) -> None:
    token = '{"token":"synthetic-token"}'
    flow = Mock()
    flow.run_local_server.return_value.to_json.return_value = token
    create_flow = Mock(return_value=flow)
    save = Mock()
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", create_flow
    )
    monkeypatch.setattr(keyring, "set_password", save)
    client_path = tmp_path / "synthetic-client.json"

    connect_gmail(client_path)

    create_flow.assert_called_once_with(str(client_path), GMAIL_SCOPES)
    flow.run_local_server.assert_called_once_with(port=0, open_browser=True)
    save.assert_called_once_with(KEYRING_SERVICE, KEYRING_ACCOUNT, token)


@pytest.mark.parametrize("token,expected", [(None, False), ("", True), ('{"token":"test"}', True)])
def test_gmail_connected_preserves_keyring_semantics(monkeypatch, token, expected: bool) -> None:
    load = Mock(return_value=token)
    monkeypatch.setattr(keyring, "get_password", load)

    assert gmail_connected() is expected
    load.assert_called_once_with(KEYRING_SERVICE, KEYRING_ACCOUNT)


def test_refresh_still_saves_to_default_keyring(monkeypatch) -> None:
    token = '{"token":"synthetic-old"}'
    updated = '{"token":"synthetic-new"}'
    credentials = SimpleNamespace(
        expired=True, refresh_token="synthetic-refresh", refresh=Mock(), to_json=lambda: updated
    )
    request = Mock()
    load = Mock(return_value=token)
    save = Mock()
    parse = Mock(return_value=credentials)
    build = Mock()
    monkeypatch.setattr(keyring, "get_password", load)
    monkeypatch.setattr(keyring, "set_password", save)
    monkeypatch.setattr("google.oauth2.credentials.Credentials.from_authorized_user_info", parse)
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: request)
    monkeypatch.setattr("googleapiclient.discovery.build", build)

    assert gmail._authorized_gmail_service() is build.return_value
    load.assert_called_once_with(KEYRING_SERVICE, KEYRING_ACCOUNT)
    parse.assert_called_once_with(json.loads(token), GMAIL_SCOPES)
    credentials.refresh.assert_called_once_with(request)
    save.assert_called_once_with(KEYRING_SERVICE, KEYRING_ACCOUNT, updated)
    build.assert_called_once_with("gmail", "v1", credentials=credentials)


def test_file_connect_and_connected_do_not_access_keyring(monkeypatch, file_store) -> None:
    token = '{"token":"synthetic-connected","refresh_token":"synthetic-refresh"}'
    flow = Mock()
    flow.run_local_server.return_value.to_json.return_value = token
    create_flow = Mock(return_value=flow)
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", create_flow
    )
    client_path = file_store.path.parent / "synthetic-client.json"

    assert not gmail_connected()
    connect_gmail(client_path)

    create_flow.assert_called_once_with(str(client_path), GMAIL_SCOPES)
    flow.run_local_server.assert_called_once_with(port=0, open_browser=True)
    assert file_store.load() == token
    assert gmail_connected()


def test_file_connect_forwards_headless_oauth_options(monkeypatch, file_store) -> None:
    token = '{"token":"synthetic-headless","refresh_token":"synthetic-refresh"}'
    flow = Mock()
    flow.run_local_server.return_value.to_json.return_value = token
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", lambda *_: flow
    )

    connect_gmail(Path("synthetic-client.json"), port=8765, open_browser=False)

    flow.run_local_server.assert_called_once_with(port=8765, open_browser=False)
    assert file_store.load() == token


@pytest.mark.parametrize(
    "expired,refresh_token", [(False, None), (True, None), (True, "synthetic")]
)
def test_file_authorization_and_refresh(monkeypatch, file_store, expired, refresh_token) -> None:
    token = '{"token":"synthetic-old"}'
    updated = '{"token":"synthetic-refreshed"}'
    file_store.save(token)
    credentials = SimpleNamespace(
        expired=expired,
        refresh_token=refresh_token,
        refresh=Mock(),
        to_json=Mock(return_value=updated),
    )
    request = Mock()
    parse = Mock(return_value=credentials)
    build = Mock()
    monkeypatch.setattr("google.oauth2.credentials.Credentials.from_authorized_user_info", parse)
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: request)
    monkeypatch.setattr("googleapiclient.discovery.build", build)

    assert gmail._authorized_gmail_service() is build.return_value
    parse.assert_called_once_with(json.loads(token), GMAIL_SCOPES)
    build.assert_called_once_with("gmail", "v1", credentials=credentials)
    if expired and refresh_token:
        credentials.refresh.assert_called_once_with(request)
        credentials.to_json.assert_called_once_with()
        assert file_store.load() == updated
    else:
        credentials.refresh.assert_not_called()
        credentials.to_json.assert_not_called()
        assert file_store.load() == token


def test_file_authorization_without_token_is_not_connected(monkeypatch, file_store) -> None:
    parse = Mock()
    build = Mock()
    monkeypatch.setattr("google.oauth2.credentials.Credentials.from_authorized_user_info", parse)
    monkeypatch.setattr("googleapiclient.discovery.build", build)

    with pytest.raises(ValueError, match="Gmail is not connected"):
        gmail._authorized_gmail_service()
    assert not gmail_connected()
    parse.assert_not_called()
    build.assert_not_called()


def test_failed_file_refresh_preserves_token(monkeypatch, file_store) -> None:
    token = '{"token":"synthetic-old"}'
    file_store.save(token)
    error = RuntimeError("invalid_grant: Token has been expired or revoked.")
    credentials = SimpleNamespace(
        expired=True, refresh_token="synthetic", refresh=Mock(side_effect=error), to_json=Mock()
    )
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_info", lambda *_: credentials
    )
    build = Mock()
    monkeypatch.setattr("googleapiclient.discovery.build", build)

    with pytest.raises(RuntimeError) as caught:
        gmail._authorized_gmail_service()
    assert caught.value is error
    assert gmail_reauthorization_required(caught.value)
    assert file_store.load() == token
    credentials.to_json.assert_not_called()
    build.assert_not_called()


def test_invalid_file_selection_does_not_start_oauth_or_fall_back(monkeypatch, file_store) -> None:
    monkeypatch.setenv("GMAIL_CREDENTIAL_FILE", "relative.json")
    create_flow = Mock()
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", create_flow
    )
    with pytest.raises(CredentialStoreError):
        connect_gmail(Path("synthetic-client.json"))
    with pytest.raises(CredentialStoreError):
        gmail_connected()
    create_flow.assert_not_called()


def test_unsafe_file_token_does_not_authorize_gmail(monkeypatch, file_store) -> None:
    file_store.save('{"token":"synthetic"}')
    file_store.path.chmod(0o644)
    parse = Mock()
    build = Mock()
    monkeypatch.setattr("google.oauth2.credentials.Credentials.from_authorized_user_info", parse)
    monkeypatch.setattr("googleapiclient.discovery.build", build)
    with pytest.raises(CredentialStoreError):
        gmail._authorized_gmail_service()
    parse.assert_not_called()
    build.assert_not_called()


def test_cancelled_file_oauth_does_not_store_credentials(monkeypatch, file_store) -> None:
    flow = Mock()
    flow.run_local_server.side_effect = RuntimeError("synthetic consent cancellation")
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", lambda *_: flow
    )

    with pytest.raises(RuntimeError, match="consent cancellation"):
        connect_gmail(Path("synthetic-client.json"), port=8765, open_browser=False)
    assert not gmail_connected()
    assert not file_store.path.exists()


def test_file_refresh_persistence_failure_does_not_build_service(monkeypatch, file_store) -> None:
    token = '{"token":"synthetic-old"}'
    file_store.save(token)
    credentials = SimpleNamespace(
        expired=True,
        refresh_token="synthetic",
        refresh=Mock(),
        to_json=lambda: '{"token":"synthetic-refreshed"}',
    )
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_info", lambda *_: credentials
    )
    build = Mock()
    monkeypatch.setattr("googleapiclient.discovery.build", build)
    save = Mock(side_effect=CredentialStoreError("synthetic storage failure"))
    monkeypatch.setattr(FileCredentialStore, "save", save)

    with pytest.raises(CredentialStoreError, match="storage failure"):
        gmail._authorized_gmail_service()
    save.assert_called_once_with('{"token":"synthetic-refreshed"}')
    assert file_store.load() == token
    build.assert_not_called()
