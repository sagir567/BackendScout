from backend_scout.gmail import GmailMessageSummary
from backend_scout.indeed_email import (
    canonical_indeed_job_url,
    collect_indeed_email_jobs,
    parse_indeed_job_page,
)


def _message(html: str) -> GmailMessageSummary:
    return GmailMessageSummary(
        message_id="indeed-1",
        thread_id="thread-1",
        from_header="Indeed Job Alerts <jobalerts-noreply@indeed.com>",
        subject="Backend jobs for you",
        date_header="Sun, 13 Sep 2026 07:00:00 +0300",
        snippet="Backend Engineer at Example Labs",
        body_html=html,
    )


def test_canonical_indeed_job_url_removes_tracking_parameters() -> None:
    url = canonical_indeed_job_url(
        "https://il.indeed.com/m/viewjob?jk=abc123&from=job-alert&tk=tracking"
    )

    assert url == "https://il.indeed.com/viewjob?jk=abc123"
    assert canonical_indeed_job_url("https://example.com/viewjob?jk=abc123") is None


def test_parse_indeed_job_page_uses_structured_job_posting() -> None:
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Junior Backend Engineer",
      "hiringOrganization": {"@type": "Organization", "name": "Example Labs"},
      "jobLocation": {
        "@type": "Place",
        "address": {"addressLocality": "Tel Aviv", "addressCountry": "Israel"}
      },
      "employmentType": "FULL_TIME",
      "datePosted": "2026-09-12",
      "description": "<p>Build Python APIs with Docker and PostgreSQL. 2+ years preferred.</p>"
    }
    </script>
    """

    job = parse_indeed_job_page("https://il.indeed.com/viewjob?jk=abc123", html)

    assert job is not None
    assert job.company == "Example Labs"
    assert job.title == "Junior Backend Engineer"
    assert job.location == "Tel Aviv, Israel"
    assert job.required_skills == ["Python", "PostgreSQL", "Docker", "APIs"]
    assert job.years_experience == "2+ years"


def test_collect_indeed_email_jobs_deduplicates_links_and_fetches_details() -> None:
    email_html = """
    <table><tr><td>
      <a href="https://il.indeed.com/m/viewjob?jk=abc123&amp;from=email">Backend Engineer</a>
      <div>Example Labs</div><div>Tel Aviv, Israel</div>
      <a href="https://il.indeed.com/viewjob?jk=abc123&amp;tk=duplicate">View job</a>
    </td></tr></table>
    """
    job_html = """
    <script type="application/ld+json">
    {
      "@type": "JobPosting",
      "title": "Backend Engineer",
      "hiringOrganization": {"name": "Example Labs"},
      "jobLocation": {"address": {"addressLocality": "Tel Aviv", "addressCountry": "Israel"}},
      "description": "Build Python backend services."
    }
    </script>
    """
    fetched: list[str] = []

    report = collect_indeed_email_jobs(
        [_message(email_html)],
        lambda url: fetched.append(url) or job_html,
    )

    assert report.inspected_messages == 1
    assert report.discovered_links == 1
    assert len(report.jobs) == 1
    assert fetched == ["https://il.indeed.com/viewjob?jk=abc123"]


def test_collect_indeed_email_jobs_falls_back_to_email_card() -> None:
    email_html = """
    <table><tr><td>
      <a href="https://il.indeed.com/viewjob?jk=fallback1">Python Backend Engineer</a>
      <div>Fallback Systems</div><div>Haifa, Israel</div>
    </td></tr></table>
    """

    report = collect_indeed_email_jobs([_message(email_html)], lambda _url: "blocked")

    assert len(report.jobs) == 1
    assert report.jobs[0].company == "Fallback Systems"
    assert report.jobs[0].location == "Haifa, Israel"
    assert report.warnings == []


def test_collect_indeed_email_jobs_resolves_cts_tracking_link_from_subject() -> None:
    tracking_url = "https://cts.indeed.com/v3/opaque-job-token"
    email_html = f'<a href="{tracking_url}">View job</a>'
    job_html = """
    <link rel="canonical" href="https://il.indeed.com/viewjob?jk=tracked123&amp;from=email">
    <script type="application/ld+json">
    {
      "@type": "JobPosting",
      "title": "Backend Engineer",
      "hiringOrganization": {"name": "Tracked Systems"},
      "jobLocation": {"address": {"addressLocality": "Haifa", "addressCountry": "Israel"}},
      "description": "Build backend services in Python."
    }
    </script>
    """
    message = GmailMessageSummary(
        message_id="tracked-email",
        thread_id=None,
        from_header="Indeed <jobs@indeed.com>",
        subject="Backend Engineer @ Tracked Systems",
        date_header="",
        snippet="",
        body_html=email_html,
    )

    report = collect_indeed_email_jobs([message], lambda url: job_html if url == tracking_url else "")

    assert report.discovered_links == 1
    assert report.jobs[0].source_url == "https://il.indeed.com/viewjob?jk=tracked123"
    assert report.jobs[0].company == "Tracked Systems"


def test_cts_fallback_prefers_title_card_and_resolved_job_key() -> None:
    tracking_url = "https://cts.indeed.com/v3/opaque-job-token"
    email_html = f"""
    <table>
      <tr><td><a href="{tracking_url}">View job</a></td></tr>
      <tr><td>
        <a href="{tracking_url}">Backend Engineer</a>
        <div>Tracked Systems</div><div>Tel Aviv, Israel</div>
      </td></tr>
    </table>
    """
    message = GmailMessageSummary(
        message_id="tracked-fallback",
        thread_id=None,
        from_header="Indeed <jobs@indeed.com>",
        subject="Backend Engineer @ Tracked Systems",
        date_header="",
        snippet="",
        body_html=email_html,
    )

    report = collect_indeed_email_jobs(
        [message],
        lambda _url: ("https://il.indeed.com/viewjob?jk=resolved123", ""),
    )

    assert report.discovered_links == 1
    assert report.jobs[0].source_url == "https://il.indeed.com/viewjob?jk=resolved123"
    assert report.jobs[0].location == "Tel Aviv, Israel"
    assert report.warnings == []


def test_collect_indeed_email_jobs_rejects_unresolved_generic_link() -> None:
    email_html = '<a href="https://il.indeed.com/viewjob?jk=unknown1">View job</a>'

    report = collect_indeed_email_jobs([_message(email_html)], lambda _url: "blocked")

    assert report.jobs == []
    assert "not contain enough job details" in report.warnings[0]
