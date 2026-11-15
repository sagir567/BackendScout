"""Classify employer emails and map them to tracked applications."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

from backend_scout.gmail import GmailMessageSummary
from backend_scout.models import ApplicationDigestItem, ApplicationStatus

DEFAULT_MAILBOX_QUERY = (
    'newer_than:30d (subject:(application OR applied OR interview OR assessment OR "coding challenge" '
    'OR "job offer" OR "offer letter" OR rejection OR recruiter OR hiring OR candidate '
    'OR מועמדות OR ראיון OR "מבחן בית" OR "הצעת עבודה") '
    'OR from:(greenhouse.io OR lever.co OR comeet.com OR ashbyhq.com OR workday.com '
    'OR smartrecruiters.com))'
)

_RECRUITMENT_CONTEXT_TERMS = (
    "application received",
    "application status",
    "application for",
    "your application",
    "thank you for applying",
    "thanks for applying",
    "applicant",
    "candidate",
    "career",
    "hiring",
    "interview",
    "job",
    "position",
    "recruit",
    "resume",
    "your cv",
    "talent acquisition",
    "coding challenge",
    "take-home",
    "take home",
    "מועמדות",
    "ראיון",
    "מבחן בית",
    "הצעת עבודה",
)
_RECRUITMENT_SENDER_TERMS = (
    "careers@",
    "jobs@",
    "recruit",
    "talent",
    "greenhouse.io",
    "lever.co",
    "comeet.com",
    "ashbyhq.com",
    "workday.com",
    "smartrecruiters.com",
)


class MailClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    status: ApplicationStatus | None
    confidence: Literal["clear", "ambiguous"]
    reason: str
    needs_immediate_attention: bool = False


def classify_gmail_message(message: GmailMessageSummary) -> MailClassification:
    subject = "" if re.match(r"^\s*(?:re|fw|fwd)\s*:", message.subject, re.IGNORECASE) else message.subject
    haystack = f"{subject} {_fresh_message_body(message)}".casefold()
    recruitment_context = is_recruitment_related_message(message)
    if recruitment_context and _contains_any(
        haystack,
        ("not moving forward", "decided not to proceed", "decided not to move forward",
         "not selected", "rejection",
         "won't be moving forward", "will not be moving forward", "לא נוכל להמשיך",
         "לא נתקדם", "החלטנו שלא להמשיך", "בחרנו שלא להתקדם"),
    ):
        return MailClassification(
            message_id=message.message_id, status=ApplicationStatus.REJECTED,
            confidence="clear", reason="Rejection language detected.",
        )
    if _contains_any(
        haystack,
        ("job offer", "employment offer", "offer letter", "employment agreement", "contract offer",
         "הצעת עבודה"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.OFFER,
            confidence="clear",
            reason="Offer-like language detected.",
            needs_immediate_attention=True,
        )
    if recruitment_context and _contains_any(
        haystack,
        ("schedule a call", "schedule an interview", "schedule your interview", "interview invitation",
         "invite you to interview", "invite you to an interview", "invite you for an interview",
         "interview confirmed",
         "was selected for an interview",
         "calendly", "זימון לראיון", "לתאם ראיון", "להזמין אותך לראיון"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.INTERVIEW,
            confidence="clear",
            reason="Interview scheduling language detected.",
            needs_immediate_attention=True,
        )
    if recruitment_context and _contains_any(
        haystack,
        ("assessment", "coding challenge", "home assignment", "take home", "hackerrank", "codility",
         "מבחן בית", "מטלת בית"),
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
        ("application received", "received your application", "thank you for applying", "thanks for applying",
         "קיבלנו את מועמדותך", "מועמדותך התקבלה"),
    ):
        return MailClassification(
            message_id=message.message_id,
            status=ApplicationStatus.RECRUITER_REPLY,
            confidence="clear",
            reason="Application confirmation language detected.",
        )
    if recruitment_context:
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


def is_recruitment_related_message(message: GmailMessageSummary) -> bool:
    """Require concrete hiring context before surfacing an inbox message."""
    haystack = _message_search_text(message)
    sender = message.from_header.casefold()
    return _contains_any(haystack, _RECRUITMENT_CONTEXT_TERMS) or _contains_any(
        sender, _RECRUITMENT_SENDER_TERMS
    )


def is_relevant_mailbox_update(
    message: GmailMessageSummary,
    classification: MailClassification,
) -> bool:
    return is_recruitment_related_message(message) and (
        classification.status is not None or classification.reason != "No application-status signal detected."
    )


def match_message_to_application(
    message: GmailMessageSummary,
    applications: list[ApplicationDigestItem],
) -> ApplicationDigestItem | None:
    haystack = _message_search_text(message)
    company_matches = [a for a in applications if a.company.casefold() in haystack]
    if len(company_matches) == 1:
        return company_matches[0]
    title_matches = [
        a for a in (company_matches or applications) if a.title.casefold() in haystack
    ]
    return title_matches[0] if len(title_matches) == 1 else None


def format_mailbox_digest(
    updates: list[tuple[GmailMessageSummary, MailClassification, ApplicationDigestItem | None]],
) -> str:
    if not updates:
        return "Mailbox scan: no clear application updates found."
    latest = {
        application.notion_page_id if application else message.message_id: (classification, application)
        for message, classification, application in updates
    }
    lines = [f"Mailbox updates: {len(latest)} application(s)."]
    for classification, application in latest.values():
        target = f"{application.company} - {application.title}" if application else "unmatched application"
        status = classification.status.value if classification.status else "needs review"
        target = " ".join(target.split())
        if len(target) > 52:
            target = target[:49] + "..."
        lines.append(f"- {target}: {status.replace('_', ' ')}")
    return "\n".join(lines)


def format_mailbox_audit_record(
    message: GmailMessageSummary,
    classification: MailClassification,
    *,
    updated_at: datetime | None = None,
) -> str:
    timestamp = (updated_at or datetime.now(UTC)).isoformat()
    status = classification.status.value if classification.status else "needs_review"
    subject = message.subject or "(no subject)"
    sender = message.from_header or "(unknown sender)"
    return (
        f"Mailbox update at {timestamp}\n"
        f"Status signal: {status}\n"
        f"Gmail message ID: {message.message_id}\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Reason: {classification.reason}"
    )


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


_QUOTED_HEADER = re.compile(r"^\s*(?:on .{1,400} wrote:|-{2,}\s*(?:original|forwarded) message\s*-*|בתאריך .+כתב(?:ה)?[:：])", re.IGNORECASE)


def _fresh_message_body(message: GmailMessageSummary) -> str:
    if message.body_text:
        body = message.body_text[:20_000]
    elif message.body_html:
        document = BeautifulSoup(message.body_html[:100_000], "html.parser")
        for element in document.select("blockquote, .gmail_quote, script, style"):
            if element.parent is not None:
                element.decompose()
        body = document.get_text("\n", strip=True)[:20_000]
    else:
        return message.snippet
    fresh = []
    for line in body.splitlines():
        if _QUOTED_HEADER.match(line):
            break
        if not line.lstrip().startswith(">"):
            fresh.append(line)
    return " ".join(fresh)


def _message_search_text(message: GmailMessageSummary) -> str:
    return f"{message.subject} {message.from_header} {_fresh_message_body(message)}".casefold()
