import base64
import json
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

KEYRING_SERVICE = "BackendScout.GmailOAuth"
KEYRING_ACCOUNT = "default"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = [GMAIL_SEND_SCOPE, GMAIL_READONLY_SCOPE]


@dataclass(frozen=True)
class GmailMessageSummary:
    message_id: str
    thread_id: str | None
    from_header: str
    subject: str
    date_header: str
    snippet: str


def connect_gmail(client_secret_path: Path) -> None:
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), GMAIL_SCOPES)
    credentials = flow.run_local_server(port=0)
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, credentials.to_json())


def gmail_connected() -> bool:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) is not None


def send_email(recipient: str, subject: str, body: str, attachment_path: Path) -> str:
    service = _authorized_gmail_service()
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
    result: dict[str, Any] = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    message_id = result.get("id")
    if not isinstance(message_id, str):
        raise TypeError("Gmail did not return a message ID")
    return message_id


def list_recent_messages(
    query: str = "newer_than:30d",
    max_results: int = 25,
) -> list[GmailMessageSummary]:
    service = _authorized_gmail_service()
    result: dict[str, Any] = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=max_results,
    ).execute()
    summaries: list[GmailMessageSummary] = []
    for item in result.get("messages", []) or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        message: dict[str, Any] = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = _header_map(message.get("payload", {}).get("headers", []))
        summaries.append(
            GmailMessageSummary(
                message_id=item["id"],
                thread_id=message.get("threadId") if isinstance(message.get("threadId"), str) else None,
                from_header=headers.get("from", ""),
                subject=headers.get("subject", ""),
                date_header=headers.get("date", ""),
                snippet=message.get("snippet", "") if isinstance(message.get("snippet"), str) else "",
            )
        )
    return summaries


def _authorized_gmail_service():
    import keyring
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    if not token:
        raise ValueError("Gmail is not connected. Run backend-scout gmail connect first.")
    credentials = Credentials.from_authorized_user_info(json.loads(token), GMAIL_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, credentials.to_json())
    return build("gmail", "v1", credentials=credentials)


def _header_map(headers: Any) -> dict[str, str]:
    mapped: dict[str, str] = {}
    if not isinstance(headers, list):
        return mapped
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            mapped[name.casefold()] = value
    return mapped
