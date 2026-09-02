import base64
import json
from email.message import EmailMessage
from pathlib import Path
from typing import Any

KEYRING_SERVICE = "BackendScout.GmailOAuth"
KEYRING_ACCOUNT = "default"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def connect_gmail(client_secret_path: Path) -> None:
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), [GMAIL_SEND_SCOPE])
    credentials = flow.run_local_server(port=0)
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, credentials.to_json())


def gmail_connected() -> bool:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) is not None


def send_email(recipient: str, subject: str, body: str, attachment_path: Path) -> str:
    import keyring
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    if not token:
        raise ValueError("Gmail is not connected. Run backend-scout gmail connect first.")
    credentials = Credentials.from_authorized_user_info(json.loads(token), [GMAIL_SEND_SCOPE])
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, credentials.to_json())
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(
        attachment_path.read_bytes(),
        maintype="application",
        subtype=attachment_path.suffix.removeprefix(".") or "octet-stream",
        filename=attachment_path.name,
    )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    result: dict[str, Any] = build("gmail", "v1", credentials=credentials).users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    message_id = result.get("id")
    if not isinstance(message_id, str):
        raise TypeError("Gmail did not return a message ID")
    return message_id
