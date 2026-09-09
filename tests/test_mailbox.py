from backend_scout.gmail import GmailMessageSummary
from backend_scout.mailbox import (
    classify_gmail_message,
    format_mailbox_audit_record,
    format_mailbox_digest,
    match_message_to_application,
)
from backend_scout.models import ApplicationDigestItem, ApplicationStatus


def _message(subject: str, snippet: str, from_header: str = "jobs@example.com") -> GmailMessageSummary:
    return GmailMessageSummary(
        message_id="msg-1",
        thread_id="thread-1",
        from_header=from_header,
        subject=subject,
        date_header="Wed, 9 Sep 2026 08:00:00 +0300",
        snippet=snippet,
    )


def test_classify_gmail_message_detects_assessment() -> None:
    classification = classify_gmail_message(_message("Coding challenge", "Please complete this assessment."))

    assert classification.status == ApplicationStatus.ASSESSMENT
    assert classification.needs_immediate_attention


def test_classify_gmail_message_detects_rejection() -> None:
    classification = classify_gmail_message(_message("Update", "Unfortunately we are not moving forward."))

    assert classification.status == ApplicationStatus.REJECTED
    assert not classification.needs_immediate_attention


def test_classify_gmail_message_leaves_ambiguous_mail_for_review() -> None:
    classification = classify_gmail_message(_message("Talent team", "We reviewed your candidate profile."))

    assert classification.status is None
    assert classification.confidence == "ambiguous"


def test_match_message_to_application_uses_company_or_role_words() -> None:
    application = ApplicationDigestItem(
        notion_page_id="page-123",
        company="Infinidat",
        title="Junior Software Developer",
        status=ApplicationStatus.SUBMITTED,
        source="comeet",
        source_url="https://example.com/job",
    )

    assert match_message_to_application(_message("Infinidat application received", ""), [application]) == application
    assert match_message_to_application(_message("Junior Software Developer update", ""), [application]) == application


def test_format_mailbox_digest_summarizes_updates() -> None:
    application = ApplicationDigestItem(
        notion_page_id="page-123",
        company="Infinidat",
        title="Junior Software Developer",
        status=ApplicationStatus.SUBMITTED,
        source="comeet",
        source_url="https://example.com/job",
    )
    message = _message("Coding challenge", "Please complete this assessment.")
    classification = classify_gmail_message(message)

    digest = format_mailbox_digest([(message, classification, application)])

    assert "Mailbox scan: 1 relevant message" in digest
    assert "Infinidat - Junior Software Developer" in digest
    assert "assessment" in digest


def test_format_mailbox_audit_record_includes_message_source() -> None:
    message = _message("Application received", "Thanks for applying.", "careers@example.com")
    classification = classify_gmail_message(message)

    record = format_mailbox_audit_record(message, classification)

    assert "Status signal: recruiter_reply" in record
    assert "Gmail message ID: msg-1" in record
    assert "careers@example.com" in record
