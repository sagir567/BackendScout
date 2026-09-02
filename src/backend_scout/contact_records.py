"""Private local records for contacts discovered from permitted sources."""

import json
from pathlib import Path

from backend_scout.contacts import VerifiedContact

CONTACTS_ROOT = Path("data/contacts")


def save_contacts(page_id: str, contacts: list[VerifiedContact], root: Path = CONTACTS_ROOT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{page_id}.json"
    payload = [{"channel": item.channel, "value": item.value, "source": item.source} for item in contacts]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_contacts(page_id: str, root: Path = CONTACTS_ROOT) -> list[VerifiedContact]:
    path = root / f"{page_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("No verified contacts recorded. Run contacts discover first.") from exc
    if not isinstance(payload, list):
        raise TypeError("Stored contact record is invalid")
    return [VerifiedContact(**item) for item in payload]


def require_verified_contact(page_id: str, channel: str, value: str) -> VerifiedContact:
    normalized = value.casefold() if channel == "email" else value.lstrip("+")
    for contact in load_contacts(page_id):
        if contact.channel == channel and contact.value == normalized:
            return contact
    raise ValueError("Recipient is not in the verified contact record for this job")
