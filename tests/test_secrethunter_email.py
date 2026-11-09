"""Synthetic fixtures only, not captured live SecretHunter alert templates."""

import json
from dataclasses import replace

import pytest

from backend_scout.gmail import GmailMessageSummary
from backend_scout.secrethunter_email import (
    canonical_secrethunter_job_url,
    collect_secrethunter_email_jobs,
)


def _message(html: str, sender: str = "SecretHunter <alerts@secrethunter.io>"):
    return GmailMessageSummary(
        message_id="private-message-token",
        thread_id="private-thread-token",
        from_header=sender,
        subject="Backend Engineer @ Not Evidence",
        date_header="",
        snippet="Private snippet must not be used",
        body_html=html,
    )


def _card(url: str = "https://secrethunter.io/search?selected=Ab12") -> str:
    return f'''<article data-title="Backend Engineer" data-company="Example Labs">
    <p>Build Python APIs with PostgreSQL. 2+ years experience.</p>
    <a href="{url}">View job</a></article>'''


def _posting(**overrides) -> str:
    posting = {
        "@type": "JobPosting",
        "title": "Backend Engineer",
        "hiringOrganization": {"name": "Example Labs"},
        "url": "https://secrethunter.io/search?selected=Json1",
        "description": "<p>Python APIs. 3+ years experience.</p>",
    }
    posting.update(overrides)
    return '<script type="application/ld+json">' + json.dumps(posting) + "</script>"


def test_semantic_card_backend_and_explicit_location():
    html = _card().replace("<article ", '<article data-location="Haifa, Israel" ')
    result = collect_secrethunter_email_jobs([_message(html)])
    assert result.inspected_messages == result.discovered_links == len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Backend Engineer"
    assert job.company == "Example Labs"
    assert job.source == "secrethunter_email"
    assert job.location == "Haifa, Israel"
    assert job.years_experience == "2+ years"
    assert "Python" in job.required_skills
    assert "PostgreSQL" in job.required_skills
    assert "Private" not in job.description
    assert result.warnings == []


def test_welcome_message_is_not_an_unsupported_job_alert():
    from dataclasses import replace

    message = replace(_message('<p>Welcome! Explore your profile.</p>'), subject="ברוכים הבאים ל-secrethunter.io")
    result = collect_secrethunter_email_jobs([message])
    assert result.jobs == []
    assert result.warnings == []


def test_jsonld_explicit_identity_description_and_location():
    html = _posting(jobLocation={"address": {"addressLocality": "Tel Aviv"}})
    result = collect_secrethunter_email_jobs([_message(html)])
    assert len(result.jobs) == 1
    assert result.jobs[0].location == "Tel Aviv"
    assert result.jobs[0].description == "Python APIs. 3+ years experience."
    assert result.jobs[0].years_experience == "3+ years"


def test_heading_and_company_label_qa_experience_is_preserved():
    html = """<table><tr><td><h3>QA Engineer</h3><p>Company: Example QA</p>
    <p>5+ years experience in QA automation with Python.</p>
    <a href="https://secrethunter.io/search?selected=QA1">View job</a>
    </td></tr></table>"""
    result = collect_secrethunter_email_jobs([_message(html)])
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "QA Engineer"
    assert result.jobs[0].years_experience == "5+ years"
    assert result.jobs[0].location is None


@pytest.mark.parametrize(
    "sender",
    [
        "SecretHunter <fake@example.com>",
        "fake@secrethunter.io.evil.test",
        "fake@evilsecrethunter.io",
        "fake@alerts.secrethunter.io",
        "secrethunter.io <fake@example.com>",
        "not-an-address",
    ],
)
def test_fake_senders_ignored(sender):
    result = collect_secrethunter_email_jobs([_message(_card(), sender)])
    assert result.inspected_messages == 1
    assert result.jobs == []
    assert result.discovered_links == 0


def test_exact_mail_subdomain_allowed():
    result = collect_secrethunter_email_jobs(
        [_message(_card(), "Alerts <alerts@mail.secrethunter.io>")]
    )
    assert len(result.jobs) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://secrethunter.io/search?selected=Ab12",
        "https://secrethunter.io.evil.test/search?selected=Ab12",
        "https://evilsecrethunter.io/search?selected=Ab12",
        "https://mail.secrethunter.io/search?selected=Ab12",
        "https://www.secrethunter.io/search?selected=Ab12",
        "https://secrethunter.io@evil.test/search?selected=Ab12",
        "https://user@secrethunter.io/search?selected=Ab12",
        "https://secrethunter.io:443/search?selected=Ab12",
        "https://secrethunter.io/jobs/Ab12",
        "https://secrethunter.io/search?selected=",
        "https://secrethunter.io/search?selected=Ab12&selected=Other",
        "https://secrethunter.io/search?selected=Ab12&selected=Ab12",
        "https://secrethunter.io/search?selected=Ab12&selected=",
        "https://secrethunter.io/search?selected=Ab12,Other",
        "https://secrethunter.io/search?selected=Ab-12",
        "https://secrethunter.io/search?selected=Ab12#signin",
        "https://secrethunter.io/signin?selected=Ab12",
        "https://secrethunter.io/profile?selected=Ab12",
        "https://secrethunter.io/unsubscribe?selected=Ab12",
        "https://secrethunter.io/upgrade?selected=Ab12",
        "https://secrethunter.firebaseapp.com/__/auth/action?oobCode=private-token",
    ],
)
def test_rejects_unobserved_unsafe_and_ambiguous_urls(url):
    assert canonical_secrethunter_job_url(url) is None
    result = collect_secrethunter_email_jobs([_message(_card(url))])
    assert result.jobs == []
    assert result.discovered_links == 0


def test_authentication_email_ignored():
    html = """<h2>Sign in to SecretHunter</h2>
    <a href="https://secrethunter.firebaseapp.com/__/auth/action?oobCode=private-token">
    Sign in</a><a href="https://secrethunter.io/profile">Profile</a>"""
    result = collect_secrethunter_email_jobs([_message(html)])
    assert result.jobs == []
    assert result.discovered_links == 0
    assert result.warnings == []


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://secrethunter.io/search?selected=A1">View jobs</a>',
        (
            "<article><h2>Backend Engineer</h2><p>Example Labs</p>"
            '<a href="https://secrethunter.io/search?selected=A1">View job</a></article>'
        ),
        _card().replace(' data-company="Example Labs"', ""),
        _posting(hiringOrganization=None),
        _posting(title=None),
        _posting(description=""),
        "Unrecognized newsletter without supported fields",
        '<a href="https://example.com/unknown">Unrecognized newsletter</a>',
    ],
)
def test_unknown_or_missing_identity_warns_without_inference_or_private_content(html):
    result = collect_secrethunter_email_jobs([_message(html)])
    assert result.jobs == []
    assert result.warnings
    assert all("private" not in warning.lower() for warning in result.warnings)
    assert all("https:" not in warning for warning in result.warnings)
    assert all("Example Labs" not in warning for warning in result.warnings)


def test_duplicates_canonicalize_and_preserve_case_sensitive_ids():
    first = _card("https://secrethunter.io/search?selected=Ab12&amp;utm_source=email")
    first = first.replace(
        "</article>", '<a href="https://secrethunter.io/search?selected=Ab12">Again</a></article>'
    )
    result = collect_secrethunter_email_jobs(
        [
            _message(first),
            _message(_card()),
            _message(_card("https://secrethunter.io/search?selected=ab12")),
        ]
    )
    assert result.inspected_messages == 3
    assert result.discovered_links == len(result.jobs) == 2
    assert result.jobs[0].source_url == "https://secrethunter.io/search?selected=Ab12"


def test_separate_cards_do_not_leak_identity_or_requirements():
    first = _card().replace(' data-company="Example Labs"', "")
    second = _card("https://secrethunter.io/search?selected=Other").replace("2+ years", "7+ years")
    result = collect_secrethunter_email_jobs([_message(f"<div>{first}{second}</div>")])
    assert result.discovered_links == 2
    assert len(result.jobs) == 1
    assert result.jobs[0].years_experience == "7+ years"
    assert result.warnings


def test_multiple_ids_in_one_card_are_ambiguous():
    html = _card().replace(
        "</article>", '<a href="https://secrethunter.io/search?selected=Other">Other</a></article>'
    )
    result = collect_secrethunter_email_jobs([_message(html)])
    assert result.jobs == []
    assert result.discovered_links == 2
    assert result.warnings


def test_conflicting_postings_for_same_id_are_skipped():
    result = collect_secrethunter_email_jobs(
        [_message(_posting() + _posting(title="Different Engineer"))]
    )
    assert result.jobs == []
    assert result.warnings


def test_conflicting_identity_across_messages_cannot_retain_first_claim():
    first = _message(_posting())
    conflict = _message(_posting(title="Different Engineer"))
    result = collect_secrethunter_email_jobs([first, conflict, first])
    assert result.jobs == []
    assert result.warnings


def test_plaintext_links_are_counted_but_not_inferred():
    message = replace(_message(""), body_text="https://secrethunter.io/search?selected=Plain1")
    result = collect_secrethunter_email_jobs([message])
    assert result.jobs == []
    assert result.discovered_links == 1
    assert result.warnings


def test_malformed_json_and_body_limit_are_safe():
    malformed = '<script type="application/ld+json">{bad private-content</script>'
    result = collect_secrethunter_email_jobs([_message(malformed), _message("x" * 500_001)])
    assert result.jobs == []
    assert len(result.warnings) == 2
    assert all("private-content" not in warning for warning in result.warnings)
