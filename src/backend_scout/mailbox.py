"""Classify employer emails and map them to tracked applications."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend_scout.gmail import GmailMessageSummary
from backend_scout.models import ApplicationDigestItem, ApplicationStatus

DEFAULT_MAILBOX_QUERY = (
    'newer_than:30d (subject:(application OR applied OR interview OR assessment OR "coding challenge" '
    'OR offer OR rejection) OR from:(greenhouse.io OR lever.co OR comeet.com OR ashbyhq.com))'
)


class MailClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    status: ApplicationStatus | None
    confidence: Literal["clear", "ambiguous"]
    reason: str
    needs_immediate_attention: bool = False


def classify_gmail_message(message: GmailMessageSummary) -> MailClassification:
    haystack = f"{message.subject} {message.from_header} {message.snippet}".casefold()
    if _contains_any(haystack, ("offer", "employment agreement", "contract offer")):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.OFFER,
            confidence="clear",
            reason="Offer-like language detected.",
            needs_immediate_attention=True,
        )
    if _contains_any(
        haystack,
        ("interview", "schedule a call", "schedule an interview", "calendly", "meet with"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.INTERVIEW,
            confidence="clear",
            reason="Interview scheduling language detected.",
            needs_immediate_attention=True,
        )
    if _contains_any(
        haystack,
        ("assessment", "coding challenge", "home assignment", "take home", "hackerrank", "codility"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.ASSESSMENT,
            confidence="clear",
            reason="Assessment or coding-challenge language detected.",
            needs_immediate_attention=True,
        )
    if _contains_any(
        haystack,
        ("not moving forward", "decided not to proceed", "unfortunately", "not selected", "rejection"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.REJECTED,
            confidence="clear",
            reason="Rejection language detected.",
        )
    if _contains_any(
        haystack,
        ("application received", "received your application", "thank you for applying", "thanks for applying"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.RECRUITER_REPLY,
            confidence="clear",
            reason="Application confirmation language detected.",
        )
    if _contains_any(haystack, ("application", "candidate", "recruit", "talent")):
        return MailClassification(
            message_id=message.message_id,
            status=None,
            confidence="ambiguous",
            reason="Recruiting-related email detected but no status change is clear.",
        )
    return MailClassification(
        message_id=message.message_id,
        status=None,
        confidence="ambiguous",
        reason="No application-status signal detected.",
    )


def match_message_to_application(
    message: GmailMessageSummary,
    applications: list[ApplicationDigestItem],
) -> ApplicationDigestItem | None:
    haystack = f"{message.subject} {message.from_header} {message.snippet}".casefold()
    for application in applications:
        company = application.company.casefold()
        title = application.title.casefold()
        title_words = [word for word in title.replace("-", " ").split() if len(word) >= 4]
        if company in haystack:
            return application
        if title_words and sum(1 for word in title_words if word in haystack) >= min(2, len(title_words)):
            return application
    return None


def format_mailbox_digest(
    updates: list[tuple[GmailMessageSummary, MailClassification, ApplicationDigestItem | None]],
) -> str:
    if not updates:
        return "Mailbox scan: no clear application updates found."
    lines = [f"Mailbox scan: {len(updates)} relevant message(s)."]
    for message, classification, application in updates:
        target = f"{application.company} - {application.title}" if application else "unmatched application"
        status = classification.status.value if classification.status else "needs review"
        lines.append("")
        lines.append(f"{target}")
        lines.append(f"Status signal: {status}")
        lines.append(f"Subject: {message.subject or '(no subject)'}")
        lines.append(f"Reason: {classification.reason}")
    return "\n".join(lines)


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)
