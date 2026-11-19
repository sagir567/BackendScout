"""Owner-only template transport; no employer delivery or automatic retries."""

import re
from dataclasses import dataclass, field

import httpx


class WhatsAppNotificationError(ValueError):
    """Safe error text: never include credentials, recipients or response bodies."""


@dataclass(frozen=True)
class OwnerWhatsAppConfig:
    enabled: bool = False
    access_token: str = field(default="", repr=False)
    phone_number_id: str = ""
    owner_number: str = field(default="", repr=False)
    template_name: str = ""
    language: str = "he"
    api_version: str = "v27.0"

    @classmethod
    def from_settings(cls, settings):
        secret = settings.owner_whatsapp_access_token
        return cls(
            enabled=settings.owner_whatsapp_enabled,
            access_token=secret.get_secret_value() if secret else "",
            phone_number_id=settings.owner_whatsapp_phone_number_id,
            owner_number=settings.owner_whatsapp_recipient,
            template_name=settings.owner_whatsapp_template_name,
            language=settings.owner_whatsapp_language,
            api_version=settings.owner_whatsapp_api_version,
        )

    def missing_fields(self) -> list[str]:
        return [name for name in ("access_token", "phone_number_id", "owner_number", "template_name")
                if not getattr(self, name).strip()]


class OwnerWhatsAppClient:
    def __init__(self, config: OwnerWhatsAppConfig, *, transport=None):
        self.config = config
        self.transport = transport

    def send_template(self, summary: str) -> str:
        """Return API acceptance ID, NOT a delivery receipt; caller owns persistence."""
        c = self.config
        if not c.enabled:
            raise WhatsAppNotificationError("WhatsApp owner notifications are disabled")
        if not (c.access_token.strip() and re.fullmatch(r"[0-9]{5,30}", c.phone_number_id)
                and re.fullmatch(r"[1-9][0-9]{7,14}", c.owner_number)
                and re.fullmatch(r"[a-z0-9_]{1,100}", c.template_name)
                and re.fullmatch(r"[a-z]{2}(?:_[A-Z]{2})?", c.language)
                and re.fullmatch(r"v[0-9]{1,2}\.0", c.api_version)):
            raise WhatsAppNotificationError("WhatsApp owner configuration is incomplete or invalid")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 900:
            raise WhatsAppNotificationError("Invalid owner notification summary")
        payload = {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": c.owner_number, "type": "template",
            "template": {"name": c.template_name, "language": {"code": c.language},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": summary.strip()},
                         ]}]},
        }
        try:
            with httpx.Client(transport=self.transport, timeout=15, follow_redirects=False) as client:
                response = client.post(
                    f"https://graph.facebook.com/{c.api_version}/{c.phone_number_id}/messages",
                    headers={"Authorization": f"Bearer {c.access_token}"}, json=payload,
                )
        except httpx.HTTPError:
            raise WhatsAppNotificationError("WhatsApp acceptance unknown; do not retry automatically") from None
        if response.status_code != 200:
            raise WhatsAppNotificationError("WhatsApp request was not acknowledged; inspect notification state")
        try:
            messages = response.json()["messages"]
            message_id = messages[0]["id"] if len(messages) == 1 else None
            if not isinstance(message_id, str) or not message_id.startswith("wamid.") or len(message_id) > 512:
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            raise WhatsAppNotificationError("WhatsApp acceptance unknown; do not retry automatically") from None
        return message_id
