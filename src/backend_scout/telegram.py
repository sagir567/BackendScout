from enum import Enum
from typing import Any, Self

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

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramApprovalAction(str, Enum):
    APPROVE_TO_TAILOR = "approve_to_tailor"
    CLOSE = "close"


class TelegramClient:
    def __init__(self, bot_token: str, base_url: str = TELEGRAM_API_BASE) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=f"{base_url}/bot{bot_token}",
            headers={"Content-Type": "application/json"},
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
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram API {method} failed: {data}")
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


def encode_callback_data(action: TelegramApprovalAction, page_id: str) -> str:
    return f"{action.value}:{page_id}"


def parse_callback_data(data: str) -> tuple[TelegramApprovalAction, str]:
    action_value, separator, page_id = data.partition(":")
    if not separator or not page_id:
        raise ValueError("Invalid callback payload")
    return TelegramApprovalAction(action_value), page_id


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
) -> str | None:
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return None

    from_user = callback_query.get("from", {})
    user_id = from_user.get("id")
    if not isinstance(user_id, int) or user_id not in allowed_user_ids:
        return None

    action, page_id = parse_callback_data(callback_query.get("data", ""))
    page = notion_client.retrieve_page(page_id)
    application = application_digest_item_from_page(page)
    next_status = _status_for_action(action)
    validate_application_status_transition(application.status, next_status)
    update_application_status(notion_client, page_id, next_status)

    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    message_id = message.get("message_id")
    chat_id = chat.get("id")
    callback_query_id = callback_query.get("id")
    if isinstance(callback_query_id, str):
        telegram_client.answer_callback_query(
            callback_query_id,
            f"{application.company} -> {next_status.value}",
        )
    if isinstance(chat_id, int) and isinstance(message_id, int):
        telegram_client.edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
        telegram_client.send_message(
            chat_id,
            f"Updated {application.company} - {application.title} to {next_status.value}.",
        )
    return next_status.value


def _status_for_action(action: TelegramApprovalAction) -> ApplicationStatus:
    if action == TelegramApprovalAction.APPROVE_TO_TAILOR:
        return ApplicationStatus.APPROVED_TO_TAILOR
    if action == TelegramApprovalAction.CLOSE:
        return ApplicationStatus.CLOSED
    raise ValueError(f"Unsupported Telegram approval action: {action.value}")
