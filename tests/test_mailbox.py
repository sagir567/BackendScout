from dataclasses import replace

from backend_scout.gmail import GmailMessageSummary
from backend_scout.mailbox import (
    classify_gmail_message,
    format_mailbox_audit_record,
    format_mailbox_digest,
    is_relevant_mailbox_update,
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


def test_reply_quote_and_snippet_do_not_replay_an_old_rejection() -> None:
    message = replace(
        _message("Re: Application rejection", "We decided not to proceed."),
        body_text="Thanks for your note.\n\nOn Tue, Recruiting wrote:\nWe decided not to proceed.",
    )
    assert classify_gmail_message(message).status is None


def test_fresh_html_reply_wins_over_the_quoted_status() -> None:
    message = replace(
        _message("Re: Your application", "We decided not to proceed."),
        body_html="<p>We invite you to an interview.</p><blockquote>We decided not to proceed.</blockquote>",
    )
    assert classify_gmail_message(message).status == ApplicationStatus.INTERVIEW


def test_classify_gmail_message_uses_full_body_text() -> None:
    message = GmailMessageSummary(
        message_id="msg-body",
        thread_id="thread-body",
        from_header="jobs@example.com",
        subject="Next step",
        date_header="",
        snippet="Open this message for details.",
        body_text="Please schedule an interview with our engineering team.",
    )

    classification = classify_gmail_message(message)

    assert classification.status == ApplicationStatus.INTERVIEW


def test_classify_gmail_message_detects_rejection() -> None:
    classification = classify_gmail_message(_message("Update", "Unfortunately we are not moving forward."))

    assert classification.status == ApplicationStatus.REJECTED
    assert not classification.needs_immediate_attention


def test_rejection_that_mentions_interview_remains_rejection():
    message = _message("Interview follow-up", "After the interview, we decided not to proceed.")
    assert classify_gmail_message(message).status == ApplicationStatus.REJECTED


def test_confirmation_mentioning_possible_interview_is_not_an_invitation():
    message = _message("Application received", "If selected, we will contact you for an interview.")
    assert classify_gmail_message(message).status == ApplicationStatus.RECRUITER_REPLY


def test_hebrew_application_rejection_and_interview():
    rejected = _message("עדכון לגבי מועמדות", "החלטנו שלא להמשיך בתהליך.")
    interview = _message("זימון לראיון", "נשמח לפגוש אותך ביום ראשון.")
    assert classify_gmail_message(rejected).status == ApplicationStatus.REJECTED
    assert classify_gmail_message(interview).status == ApplicationStatus.INTERVIEW


def test_classify_gmail_message_leaves_ambiguous_mail_for_review() -> None:
    classification = classify_gmail_message(_message("Talent team", "We reviewed your candidate profile."))

    assert classification.status is None
    assert classification.confidence == "ambiguous"


def test_commercial_offer_is_not_a_job_offer() -> None:
    message = _message(
        "Update to the Apple Arcade offer included with your device",
        "Enjoy three months free with your recent purchase.",
        "no_reply@apple.com",
    )

    classification = classify_gmail_message(message)

    assert classification.status is None
    assert not is_relevant_mailbox_update(message, classification)


def test_visa_marketing_offer_is_not_a_job_offer() -> None:
    message = _message(
        "A special Visa offer",
        "Save on your next card purchase with this limited promotion.",
        "offers@visa.com",
    )

    classification = classify_gmail_message(message)

    assert classification.status is None
    assert not is_relevant_mailbox_update(message, classification)


def test_employment_offer_remains_relevant() -> None:
    message = _message(
        "Employment offer for Backend Engineer",
        "We are pleased to send your offer letter.",
        "careers@example.com",
    )

    classification = classify_gmail_message(message)

    assert classification.status == ApplicationStatus.OFFER
    assert is_relevant_mailbox_update(message, classification)


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
    other = application.model_copy(update={"notion_page_id": "other", "title": "QA Engineer"})
    assert match_message_to_application(_message("Infinidat update", ""), [application, other]) is None
    assert match_message_to_application(
        _message("Infinidat - Junior Software Developer", ""), [other, application]
    ) == application


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

    assert "Mailbox updates: 1 application" in digest
    assert "Infinidat - Junior Software Developer" in digest
    assert "assessment" in digest


def test_format_mailbox_audit_record_includes_message_source() -> None:
    message = _message("Application received", "Thanks for applying.", "careers@example.com")
    classification = classify_gmail_message(message)

    record = format_mailbox_audit_record(message, classification)

    assert "Status signal: recruiter_reply" in record
    assert "Gmail message ID: msg-1" in record
    assert "careers@example.com" in record
