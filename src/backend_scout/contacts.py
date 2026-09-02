import re
from dataclasses import dataclass

from backend_scout.models import Job

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
WHATSAPP_PATTERN = re.compile(r"(?:wa\.me/|whatsapp\.com/)(\+?[0-9]{7,15})", re.IGNORECASE)


@dataclass(frozen=True)
class VerifiedContact:
    channel: str
    value: str
    source: str


def discover_job_post_contacts(job: Job) -> list[VerifiedContact]:
    contacts = [VerifiedContact("email", value.casefold(), "job_post") for value in EMAIL_PATTERN.findall(job.description)]
    contacts.extend(
        VerifiedContact("whatsapp", number.lstrip("+"), "job_post")
        for number in WHATSAPP_PATTERN.findall(job.description)
    )
    return list(dict.fromkeys(contacts))


def discover_official_page_contacts(html: str) -> list[VerifiedContact]:
    contacts = [VerifiedContact("email", value.casefold(), "official_site") for value in EMAIL_PATTERN.findall(html)]
    contacts.extend(
        VerifiedContact("whatsapp", number.lstrip("+"), "official_site")
        for number in WHATSAPP_PATTERN.findall(html)
    )
    return list(dict.fromkeys(contacts))
