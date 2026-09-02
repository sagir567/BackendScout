import json
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Self

from backend_scout.cv_artifacts import load_manifest, verify_manifest
from backend_scout.models import (
    ApplicationDigestItem,
    ApplicationStatus,
    validate_application_status_transition,
)
from backend_scout.notion import (
    NotionClient,
    application_digest_item_from_page,
    update_application_status,
)
from backend_scout.revisions import save_revision_feedback

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramApprovalAction(str, Enum):
    APPROVE_TO_TAILOR = "approve_to_tailor"
    APPROVE_TO_SUBMIT = "approve_to_submit"
    REQUEST_REVISION = "request_revision"
    SEND_EMAIL = "send_email"
    CONFIRM_WHATSAPP = "confirm_whatsapp"
    CLOSE = "close"


class TelegramClient:
    def __init__(self, bot_token: str, base_url: str = TELEGRAM_API_BASE) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=f"{base_url}/bot{bot_token}",
            timeout=30.0,
        )

    def get_me(self) -> dict[str, Any]:
        return self._post("getMe")

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self._post("getUpdates", payload).get("result", [])

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._post("sendMessage", payload)

    def send_document(
        self,
        chat_id: int,
        document_path: Path,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": str(chat_id), "caption": caption}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        with document_path.open("rb") as document:
            response = self._client.post(
                "/sendDocument",
                data=payload,
                files={"document": (document_path.name, document)},
            )
        return self._parse_response(response, "sendDocument")

    def answer_callback_query(self, callback_query_id: str, text: str) -> dict[str, Any]:
        return self._post("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._post("editMessageReplyMarkup", payload)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _post(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.post(f"/{method}", json=payload or {})
        return self._parse_response(response, method)

    @staticmethod
    def _parse_response(response: Any, method: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.is_error or not data.get("ok"):
            detail = data.get("description") or f"HTTP {response.status_code}"
            raise ValueError(f"Telegram API {method} failed: {detail}")
        return data


def format_digest_message(item: ApplicationDigestItem) -> str:
    lines = [
        f"{item.company} - {item.title}",
        f"Status: {item.status.value}",
        f"Score: {item.match_score if item.match_score is not None else 'unknown'}",
        f"Location: {item.location or 'unknown'}",
        f"Remote: {item.remote_policy or 'unknown'}",
        f"Salary: {item.salary_text or 'unknown'}",
    ]
    if item.required_skills:
        lines.append(f"Skills: {', '.join(item.required_skills[:6])}")
    if item.match_reason:
        lines.append(f"Why it fits: {item.match_reason}")
    lines.append(f"Source: {item.source_url}")
    return "\n".join(lines)


def build_digest_reply_markup(page_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve tailoring",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.APPROVE_TO_TAILOR, page_id
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id),
                },
            ]
        ]
    }


def build_cv_draft_reply_markup(page_id: str, draft_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve this CV",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.APPROVE_TO_SUBMIT, page_id, draft_id
                    ),
                },
                {
                    "text": "Request changes",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.REQUEST_REVISION, page_id, draft_id
                    ),
                },
            ],
            [
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id),
                },
            ]
        ]
    }


def build_email_review_reply_markup(page_id: str, review_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Send approved email",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.SEND_EMAIL, page_id, review_id
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id),
                },
            ]
        ]
    }


def build_whatsapp_handoff_reply_markup(page_id: str, handoff_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "I sent this WhatsApp",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.CONFIRM_WHATSAPP, page_id, handoff_id
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id),
                },
            ]
        ]
    }


def encode_callback_data(
    action: TelegramApprovalAction,
    page_id: str,
    draft_id: str | None = None,
) -> str:
    payload = f"{action.value}:{page_id}"
    if draft_id:
        payload = f"{payload}:{draft_id}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Telegram callback payload exceeds 64 bytes")
    return payload


def parse_callback_data(data: str) -> tuple[TelegramApprovalAction, str, str | None]:
    action_value, separator, remainder = data.partition(":")
    page_id, _draft_separator, draft_id = remainder.partition(":")
    if not separator or not page_id:
        raise ValueError("Invalid callback payload")
    return TelegramApprovalAction(action_value), page_id, draft_id or None


def send_digest_messages(
    telegram_client: TelegramClient,
    notion_client: NotionClient,
    chat_id: int,
    items: list[ApplicationDigestItem],
) -> list[dict[str, Any]]:
    sent_messages: list[dict[str, Any]] = []
    for item in items:
        sent_messages.append(
            telegram_client.send_message(
                chat_id,
                format_digest_message(item),
                reply_markup=build_digest_reply_markup(item.notion_page_id),
            )
        )
        if item.status == ApplicationStatus.FOUND:
            update_application_status(
                notion_client,
                item.notion_page_id,
                ApplicationStatus.DIGEST_SENT,
            )
    return sent_messages


def process_telegram_update(
    telegram_client: TelegramClient,
    notion_client: NotionClient,
    update: dict[str, Any],
    allowed_user_ids: set[int],
    cv_archive_root: Path | None = None,
    email_delivery_handler: Callable[[str, str], None] | None = None,
    whatsapp_confirmation_handler: Callable[[str, str], None] | None = None,
) -> str | None:
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return _process_revision_message(telegram_client, notion_client, update, allowed_user_ids)

    from_user = callback_query.get("from", {})
    user_id = from_user.get("id")
    if not isinstance(user_id, int) or user_id not in allowed_user_ids:
        return None

    action, page_id, draft_id = parse_callback_data(callback_query.get("data", ""))
    page = notion_client.retrieve_page(page_id)
    application = application_digest_item_from_page(page)
    next_status = _status_for_action(action)
    if action in {
        TelegramApprovalAction.APPROVE_TO_SUBMIT,
        TelegramApprovalAction.REQUEST_REVISION,
    }:
        if cv_archive_root is None or not draft_id:
            raise ValueError("CV review action requires an exact draft artifact")
        manifest = load_manifest(cv_archive_root, application.company, page_id)
        verify_manifest(manifest, draft_id)
    validate_application_status_transition(application.status, next_status)
    if action == TelegramApprovalAction.SEND_EMAIL:
        if not draft_id or email_delivery_handler is None:
            raise ValueError("Email delivery requires a reviewed email record")
        email_delivery_handler(page_id, draft_id)
    if action == TelegramApprovalAction.CONFIRM_WHATSAPP:
        if not draft_id or whatsapp_confirmation_handler is None:
            raise ValueError("WhatsApp confirmation requires a prepared handoff record")
        whatsapp_confirmation_handler(page_id, draft_id)
    if action not in {
        TelegramApprovalAction.SEND_EMAIL,
        TelegramApprovalAction.CONFIRM_WHATSAPP,
    }:
        update_application_status(notion_client, page_id, next_status)

    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    message_id = message.get("message_id")
    chat_id = chat.get("id")
    callback_query_id = callback_query.get("id")
    if isinstance(callback_query_id, str):
        try:
            telegram_client.answer_callback_query(
                callback_query_id,
                f"{application.company} -> {next_status.value}",
            )
        except ValueError:
            # Telegram callback acknowledgements expire quickly. The durable Notion
            # transition has already succeeded and must not be rolled back by UI feedback.
            pass
    if isinstance(chat_id, int) and isinstance(message_id, int):
        try:
            telegram_client.edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
            message_text = f"Updated {application.company} - {application.title} to {next_status.value}."
            if action == TelegramApprovalAction.REQUEST_REVISION:
                message_text = (
                    f"Send revision feedback as /revise_{page_id} followed by the changes you want."
                )
            telegram_client.send_message(
                chat_id,
                message_text,
            )
        except ValueError:
            pass
    return next_status.value


def _status_for_action(action: TelegramApprovalAction) -> ApplicationStatus:
    if action == TelegramApprovalAction.APPROVE_TO_TAILOR:
        return ApplicationStatus.APPROVED_TO_TAILOR
    if action == TelegramApprovalAction.APPROVE_TO_SUBMIT:
        return ApplicationStatus.APPROVED_TO_SUBMIT
    if action == TelegramApprovalAction.REQUEST_REVISION:
        return ApplicationStatus.REVISION_REQUESTED
    if action == TelegramApprovalAction.SEND_EMAIL:
        return ApplicationStatus.SUBMITTED
    if action == TelegramApprovalAction.CONFIRM_WHATSAPP:
        return ApplicationStatus.SUBMITTED
    if action == TelegramApprovalAction.CLOSE:
        return ApplicationStatus.CLOSED
    raise ValueError(f"Unsupported Telegram approval action: {action.value}")


def _process_revision_message(
    telegram_client: TelegramClient,
    notion_client: NotionClient,
    update: dict[str, Any],
    allowed_user_ids: set[int],
) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from", {})
    user_id = sender.get("id")
    text = message.get("text")
    if not isinstance(user_id, int) or user_id not in allowed_user_ids or not isinstance(text, str):
        return None
    command, separator, feedback = text.partition(" ")
    if not separator or not command.startswith("/revise_"):
        return None
    page_id = command.removeprefix("/revise_")
    application = application_digest_item_from_page(notion_client.retrieve_page(page_id))
    if application.status != ApplicationStatus.REVISION_REQUESTED:
        raise ValueError("Revision feedback is only accepted after Request changes")
    save_revision_feedback(page_id, feedback)
    chat = message.get("chat", {})
    if isinstance(chat.get("id"), int):
        telegram_client.send_message(
            chat["id"],
            f"Saved revision feedback for {application.company}. Run cv revise to create versioned files.",
        )
    return "revision_feedback_saved"
