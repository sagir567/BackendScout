import json
from collections import Counter
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Self

from backend_scout.config import TrackerName
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
from backend_scout.tailoring_notes import save_tailoring_note
from backend_scout.task_queue import QueuedTaskKind, enqueue_task

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramApprovalAction(str, Enum):
    APPROVE_TO_TAILOR = "approve_to_tailor"
    APPROVE_TO_SUBMIT = "approve_to_submit"
    REQUEST_REVISION = "request_revision"
    SEND_EMAIL = "send_email"
    CONFIRM_WHATSAPP = "confirm_whatsapp"
    AUTHORIZE_PORTAL_SUBMIT = "authorize_portal_submit"
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

    def send_photo(
        self,
        chat_id: int,
        photo_path: Path,
        caption: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": str(chat_id), "caption": caption}
        with photo_path.open("rb") as photo:
            response = self._client.post(
                "/sendPhoto",
                data=payload,
                files={"photo": (photo_path.name, photo)},
            )
        return self._parse_response(response, "sendPhoto")

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


def build_digest_reply_markup(page_id: str, tracker: TrackerName = TrackerName.TEST) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve tailoring",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.APPROVE_TO_TAILOR, page_id, tracker=tracker
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id, tracker=tracker),
                },
            ]
        ]
    }


def build_cv_draft_reply_markup(
    page_id: str, draft_id: str, tracker: TrackerName = TrackerName.TEST
) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve this CV",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.APPROVE_TO_SUBMIT, page_id, draft_id, tracker
                    ),
                },
                {
                    "text": "Request changes",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.REQUEST_REVISION, page_id, draft_id, tracker
                    ),
                },
            ],
            [
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id, tracker=tracker),
                },
            ]
        ]
    }


def build_email_review_reply_markup(
    page_id: str, review_id: str, tracker: TrackerName = TrackerName.TEST
) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Send approved email",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.SEND_EMAIL, page_id, review_id, tracker
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id, tracker=tracker),
                },
            ]
        ]
    }


def build_whatsapp_handoff_reply_markup(
    page_id: str, handoff_id: str, tracker: TrackerName = TrackerName.TEST
) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "I sent this WhatsApp",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.CONFIRM_WHATSAPP, page_id, handoff_id, tracker
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(TelegramApprovalAction.CLOSE, page_id, tracker=tracker),
                },
            ]
        ]
    }


def build_portal_submit_reply_markup(
    page_id: str, authorization_id: str, tracker: TrackerName = TrackerName.TEST
) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Submit now",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT,
                        page_id,
                        authorization_id,
                        tracker,
                    ),
                },
                {
                    "text": "Close",
                    "callback_data": encode_callback_data(
                        TelegramApprovalAction.CLOSE, page_id, tracker=tracker
                    ),
                },
            ]
        ]
    }


def encode_callback_data(
    action: TelegramApprovalAction,
    page_id: str,
    draft_id: str | None = None,
    tracker: TrackerName = TrackerName.TEST,
) -> str:
    action_code = {
        TelegramApprovalAction.APPROVE_TO_TAILOR: "t",
        TelegramApprovalAction.APPROVE_TO_SUBMIT: "s",
        TelegramApprovalAction.REQUEST_REVISION: "r",
        TelegramApprovalAction.SEND_EMAIL: "e",
        TelegramApprovalAction.CONFIRM_WHATSAPP: "w",
        TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT: "p",
        TelegramApprovalAction.CLOSE: "c",
    }[action]
    payload = f"{action_code}:{tracker.value[0]}:{page_id}"
    if draft_id:
        payload = f"{payload}:{draft_id}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Telegram callback payload exceeds 64 bytes")
    return payload


def parse_callback_data(data: str) -> tuple[TelegramApprovalAction, str, str | None, TrackerName]:
    parts = data.split(":")
    if len(parts) < 2:
        raise ValueError("Invalid callback payload")
    legacy_actions = {action.value for action in TelegramApprovalAction}
    if parts[0] in legacy_actions:
        return TelegramApprovalAction(parts[0]), parts[1], parts[2] if len(parts) > 2 else None, TrackerName.TEST
    action = {
        "t": TelegramApprovalAction.APPROVE_TO_TAILOR,
        "s": TelegramApprovalAction.APPROVE_TO_SUBMIT,
        "r": TelegramApprovalAction.REQUEST_REVISION,
        "e": TelegramApprovalAction.SEND_EMAIL,
        "w": TelegramApprovalAction.CONFIRM_WHATSAPP,
        "p": TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT,
        "c": TelegramApprovalAction.CLOSE,
    }.get(parts[0])
    tracker = {"t": TrackerName.TEST, "p": TrackerName.PRODUCTION}.get(parts[1])
    if action is None or tracker is None or len(parts) < 3 or not parts[2]:
        raise ValueError("Invalid callback payload")
    return action, parts[2], parts[3] if len(parts) > 3 else None, tracker


def send_digest_messages(
    telegram_client: TelegramClient,
    notion_client: NotionClient,
    chat_id: int,
    items: list[ApplicationDigestItem],
    tracker: TrackerName = TrackerName.TEST,
) -> list[dict[str, Any]]:
    sent_messages: list[dict[str, Any]] = []
    for item in items:
        sent_messages.append(
            telegram_client.send_message(
                chat_id,
                format_digest_message(item),
                reply_markup=build_digest_reply_markup(item.notion_page_id, tracker),
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
    tracker: TrackerName = TrackerName.TEST,
    data_source_id: str | None = None,
    portal_submission_authorization_handler: Callable[[str, str], None] | None = None,
    task_enqueue_handler: Callable[[QueuedTaskKind, TrackerName, dict[str, Any]], str] | None = None,
) -> str | None:
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return _process_message(
            telegram_client,
            notion_client,
            update,
            allowed_user_ids,
            tracker,
            data_source_id,
            task_enqueue_handler,
        )

    from_user = callback_query.get("from", {})
    user_id = from_user.get("id")
    if not isinstance(user_id, int) or user_id not in allowed_user_ids:
        return None

    action, page_id, draft_id, callback_tracker = parse_callback_data(callback_query.get("data", ""))
    if callback_tracker != tracker:
        raise ValueError("Telegram action belongs to a different tracker")
    callback_query_id = callback_query.get("id")
    if isinstance(callback_query_id, str):
        try:
            telegram_client.answer_callback_query(callback_query_id, "Received. Processing...")
        except ValueError:
            pass
    page = notion_client.retrieve_page(page_id)
    _assert_page_tracker(page, data_source_id)
    application = application_digest_item_from_page(page)
    if action == TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT:
        if not draft_id or portal_submission_authorization_handler is None:
            raise ValueError("Portal submission requires a pending final approval")
        if application.status != ApplicationStatus.SUBMISSION_PREPARED:
            raise ValueError("Portal submission can only be authorized after browser preparation")
        portal_submission_authorization_handler(page_id, draft_id)
        next_status = application.status
    else:
        next_status = _status_for_action(action)
    if action in {
        TelegramApprovalAction.APPROVE_TO_SUBMIT,
        TelegramApprovalAction.REQUEST_REVISION,
    }:
        if cv_archive_root is None or not draft_id:
            raise ValueError("CV review action requires an exact draft artifact")
        manifest = load_manifest(cv_archive_root, application.company, page_id)
        verify_manifest(manifest, draft_id)
    if action != TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT:
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
        TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT,
    }:
        update_application_status(notion_client, page_id, next_status)

    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    message_id = message.get("message_id")
    chat_id = chat.get("id")
    queued_task_id: str | None = None
    if (
        action == TelegramApprovalAction.APPROVE_TO_TAILOR
        and task_enqueue_handler is not None
        and isinstance(chat_id, int)
    ):
        queued_task_id = task_enqueue_handler(
            QueuedTaskKind.CV_DRAFT,
            tracker,
            {"page_id": page_id, "chat_id": chat_id},
        )
    if isinstance(chat_id, int) and isinstance(message_id, int):
        try:
            telegram_client.edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
            message_text = f"Updated {application.company} - {application.title} to {next_status.value}."
            if queued_task_id:
                message_text += f" Queued CV draft task {queued_task_id}."
            if action == TelegramApprovalAction.REQUEST_REVISION:
                message_text = (
                    f"Send revision feedback as /revise_{page_id} followed by the changes you want."
                )
            elif action == TelegramApprovalAction.APPROVE_TO_SUBMIT:
                message_text = (
                    f"CV approved for {application.company}. "
                    f"Send /prepare_{page_id} when you want BackendScout to prepare the portal."
                )
            elif action == TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT:
                message_text = (
                    f"Final portal submission approved for {application.company}. "
                    "Run apply resume within 15 minutes."
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
    if action == TelegramApprovalAction.AUTHORIZE_PORTAL_SUBMIT:
        return ApplicationStatus.SUBMISSION_PREPARED
    if action == TelegramApprovalAction.CLOSE:
        return ApplicationStatus.CLOSED
    raise ValueError(f"Unsupported Telegram approval action: {action.value}")


def _process_message(
    telegram_client: TelegramClient,
    notion_client: NotionClient,
    update: dict[str, Any],
    allowed_user_ids: set[int],
    tracker: TrackerName,
    data_source_id: str | None,
    task_enqueue_handler: Callable[[QueuedTaskKind, TrackerName, dict[str, Any]], str] | None = None,
) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from", {})
    user_id = sender.get("id")
    text = message.get("text")
    if not isinstance(user_id, int) or user_id not in allowed_user_ids or not isinstance(text, str):
        return None
    command, _, feedback = text.strip().partition(" ")
    feedback = feedback.strip()
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if command.casefold() in {"/status", "status"}:
        counts = _application_status_counts(notion_client, data_source_id)
        confirmation = _format_status_counts(counts, tracker)
        result = "status_sent"
    elif command.casefold() in {"/scout", "/scout_today", "scout"} or text.strip().casefold() in {
        "run scout",
        "run today's scout",
        "run todays scout",
    }:
        payload: dict[str, Any] = {}
        if isinstance(chat_id, int):
            payload["chat_id"] = chat_id
        task_id = _enqueue_task(task_enqueue_handler, QueuedTaskKind.SCOUT_TODAY, tracker, payload)
        confirmation = f"Queued today's {tracker.value} scout. Task: {task_id}."
        result = "scout_queued"
    elif command.startswith("/draft_"):
        page_id = command.removeprefix("/draft_")
        if not isinstance(chat_id, int):
            return None
        task_id = _enqueue_task(
            task_enqueue_handler,
            QueuedTaskKind.CV_DRAFT,
            tracker,
            {"page_id": page_id, "chat_id": chat_id},
        )
        confirmation = f"Queued CV draft task {task_id}."
        result = "cv_draft_queued"
    elif command.startswith("/prepare_"):
        page_id = command.removeprefix("/prepare_")
        if not isinstance(chat_id, int):
            return None
        task_id = _enqueue_task(
            task_enqueue_handler,
            QueuedTaskKind.PORTAL_PREPARE,
            tracker,
            {"page_id": page_id, "chat_id": chat_id},
        )
        confirmation = f"Queued portal preparation task {task_id}."
        result = "portal_prepare_queued"
    elif command.startswith("/revise_"):
        if not feedback:
            raise ValueError("Revision feedback cannot be empty")
        page_id = command.removeprefix("/revise_")
        page = notion_client.retrieve_page(page_id)
        _assert_page_tracker(page, data_source_id)
        application = application_digest_item_from_page(page)
        if application.status != ApplicationStatus.REVISION_REQUESTED:
            raise ValueError("Revision feedback is only accepted after Request changes")
        save_revision_feedback(page_id, feedback)
        confirmation = f"Saved revision feedback for {application.company}. Run cv revise to create versioned files."
        result = "revision_feedback_saved"
    elif command.startswith("/tailor_"):
        if not feedback:
            raise ValueError("Tailoring note cannot be empty")
        page_id = command.removeprefix("/tailor_")
        page = notion_client.retrieve_page(page_id)
        _assert_page_tracker(page, data_source_id)
        application = application_digest_item_from_page(page)
        if application.status not in {ApplicationStatus.DIGEST_SENT, ApplicationStatus.APPROVED_TO_TAILOR}:
            raise ValueError("Tailoring notes are accepted only before a CV draft is created")
        save_tailoring_note(page_id, tracker.value, feedback)
        confirmation = f"Saved tailoring note for {application.company}. It will guide the next evidence-only CV draft."
        result = "tailoring_note_saved"
    else:
        return None
    if isinstance(chat_id, int):
        telegram_client.send_message(
            chat_id,
            confirmation,
        )
    return result


def _enqueue_task(
    task_enqueue_handler: Callable[[QueuedTaskKind, TrackerName, dict[str, Any]], str] | None,
    kind: QueuedTaskKind,
    tracker: TrackerName,
    payload: dict[str, Any],
) -> str:
    if task_enqueue_handler is not None:
        return task_enqueue_handler(kind, tracker, payload)
    return enqueue_task(kind, tracker, payload).task_id


def _assert_page_tracker(page: dict[str, Any], data_source_id: str | None) -> None:
    if data_source_id is None:
        return
    parent = page.get("parent")
    if not isinstance(parent, dict):
        return
    page_data_source_id = parent.get("data_source_id")
    if page_data_source_id is not None and page_data_source_id != data_source_id:
        raise ValueError("Notion page belongs to a different tracker")


def _application_status_counts(notion_client: NotionClient, data_source_id: str | None) -> Counter[str]:
    if data_source_id is None:
        return Counter()
    result = notion_client.query_data_source(data_source_id, {"page_size": 100})
    counts: Counter[str] = Counter()
    for page in result.get("results", []):
        if not isinstance(page, dict):
            continue
        counts[application_digest_item_from_page(page).status.value] += 1
    return counts


def _format_status_counts(counts: Counter[str], tracker: TrackerName) -> str:
    if not counts:
        return f"BackendScout {tracker.value} status: no tracked applications found."
    parts = [f"{status}: {count}" for status, count in sorted(counts.items())]
    return f"BackendScout {tracker.value} status\n" + "\n".join(parts)
