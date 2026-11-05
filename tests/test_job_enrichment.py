"""Synthetic public posting fixtures only. Every request uses MockTransport."""

import gzip
import json
from dataclasses import FrozenInstanceError

import httpx
import pytest

from backend_scout import job_enrichment as enrichment
from backend_scout.job_enrichment import enrich_job
from backend_scout.models import Job

UUID = "9495066e-ebcc-49ed-bac5-0480be15753f"
OTHER_UUID = "11111111-2222-3333-4444-555555555555"
LINKEDIN = "https://www.linkedin.com/jobs/view/4478499323"
LEVER_EU = f"https://jobs.eu.lever.co/mobileye/{UUID}/apply?lever-source=Job%20postings%20feed"
BODY = (
    "Build reliable Python services and APIs for our engineering team. "
    "Responsibilities include designing distributed systems, writing automated tests, "
    "reviewing production changes and collaborating with product engineers. "
    "Requirements: 3+ years of experience developing backend software with PostgreSQL, "
    "Docker and Linux. Maintain operational documentation and investigate service incidents."
)


def _job(**updates):
    values = {
        "source": "lin_srael:Lin-Srael",
        "source_url": LINKEDIN,
        "company": "Example Labs",
        "title": "Backend Engineer",
        "description": "Published Lin-Srael job listing.",
        "required_skills": ["Java"],
        "years_experience": "9+ years",
        "match_score": 75,
        "location": "Israel",
        "match_reason": "Existing score; not refreshed here.",
    }
    values.update(updates)
    return Job(**values)


def _fetcher(handler):
    # No network-capable transport is constructed by these fixtures.
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return lambda url: client.send(client.build_request("GET", url), stream=True)


def _schema(**updates):
    values = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "url": LINKEDIN,
        "title": "Backend Engineer",
        "hiringOrganization": {"name": "Example Labs"},
        "description": f"<p>{BODY}</p>",
    }
    values.update(updates)
    return values


def _html(payload=None, *, canonical=LINKEDIN, title="Backend Engineer", extra=""):
    script = (
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        if payload is not None
        else ""
    )
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f"<html><head><title>{title}</title>{link}{script}</head><body>{extra}</body></html>"


def _page_fetch(payload=None, **html_options):
    return _fetcher(
        lambda request: httpx.Response(
            200,
            text=_html(payload, **html_options),
            headers={"content-type": "text/html"},
        )
    )


def _gh(**updates):
    item = {
        "id": 12345,
        "title": "Backend Engineer",
        "company_name": "Example Labs",
        "content": f"<p>{BODY}</p>",
        "absolute_url": "https://job-boards.greenhouse.io/example/jobs/12345",
    }
    item.update(updates)
    return item


def _lever(**updates):
    item = {
        "id": UUID,
        "text": "Backend Engineer",
        "descriptionPlain": BODY,
        "hostedUrl": f"https://jobs.eu.lever.co/mobileye/{UUID}",
        "applyUrl": f"https://jobs.eu.lever.co/mobileye/{UUID}/apply",
    }
    item.update(updates)
    return item


def _json_fetch(payload, requested=None):
    def handle(request):
        if requested is not None:
            requested.append(request)
        return httpx.Response(200, json=payload)

    return _fetcher(handle)


def test_greenhouse_exact_job_uses_public_content_endpoint_and_preserves_input():
    original = _job(application_url="https://boards.greenhouse.io/example/jobs/12345")
    before = original.model_dump()
    requested = []
    result = enrich_job(original, fetcher=_json_fetch(_gh(), requested))

    assert result.status == "enriched"
    assert result.reason_code == "full_description_found"
    assert (
        str(requested[0].url)
        == "https://boards-api.greenhouse.io/v1/boards/example/jobs/12345?content=true"
    )
    assert requested[0].method == "GET"
    assert result.job.description == BODY
    assert result.job.years_experience == "3+ years"
    assert result.job.required_skills == [
        "Python",
        "PostgreSQL",
        "Docker",
        "Linux",
        "APIs",
        "Distributed Systems",
    ]
    assert original.model_dump() == before
    for field in (
        "company",
        "title",
        "source",
        "source_url",
        "discovered_at",
        "updated_at",
        "match_score",
        "match_reason",
        "location",
    ):
        assert getattr(result.job, field) == getattr(original, field)
    assert result.evidence_urls == (str(requested[0].url),)
    assert result.evidence_url == str(requested[0].url)
    result.job.required_skills.append("Synthetic change")
    assert original.required_skills == ["Java"]
    with pytest.raises(FrozenInstanceError):
        result.status = "unavailable"


@pytest.mark.parametrize("region", ["", "eu."])
def test_lever_global_and_eu_exact_uuid_including_requirements_and_closing(region):
    host = f"jobs.{region}lever.co"
    job = _job(
        application_url=f"https://{host}/example/{UUID}/apply?lever-source=Job%20postings%20feed"
    )
    requested = []
    payload = _lever(
        hostedUrl=f"https://{host}/example/{UUID}",
        applyUrl=f"https://{host}/example/{UUID}/apply",
        descriptionPlain="Our team builds reliable backend products for customers.",
        lists=[{"text": "Requirements", "content": f"<li>{BODY}</li>"}],
        additionalPlain="We offer an inclusive workplace with opportunities for mentoring.",
        salaryDescriptionPlain="Compensation is published by the employer.",
    )
    result = enrich_job(job, fetcher=_json_fetch(payload, requested))
    assert result.status == "enriched"
    assert (
        str(requested[0].url)
        == f"https://api.{region}lever.co/v0/postings/example/{UUID}?mode=json"
    )
    assert len(requested) == 1
    assert "Requirements" in result.job.description
    assert BODY in result.job.description
    assert "inclusive workplace" in result.job.description
    assert "Compensation is published" in result.job.description
    assert result.job.application_url == f"https://{host}/example/{UUID}/apply"
    assert result.job.source_url == LINKEDIN


def test_official_eu_url_is_tried_before_linkedin():
    requested = []
    result = enrich_job(_job(application_url=LEVER_EU), fetcher=_json_fetch(_lever(), requested))
    assert result.status == "enriched"
    assert [request.url.host for request in requested] == ["api.eu.lever.co"]


def test_ashby_matches_exact_uuid_url_not_first_same_title():
    requested = []
    exact_url = f"https://jobs.ashbyhq.com/example/{UUID}"
    payload = {
        "jobs": [
            {
                "title": "Backend Engineer",
                "jobUrl": f"https://jobs.ashbyhq.com/example/{OTHER_UUID}",
                "descriptionPlain": "Wrong role",
            },
            {
                "title": "Backend Engineer",
                "jobUrl": exact_url,
                "descriptionHtml": f"<p>{BODY}</p>",
                "applyUrl": f"{exact_url}/application",
            },
        ]
    }
    result = enrich_job(_job(application_url=exact_url), fetcher=_json_fetch(payload, requested))
    assert result.status == "enriched"
    assert result.job.description == BODY
    assert result.job.application_url == f"{exact_url}/application"
    assert str(requested[0].url) == "https://api.ashbyhq.com/posting-api/job-board/example"


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"jobs": []}, "closed"),
        (
            {
                "jobs": [
                    {
                        "jobUrl": f"https://jobs.ashbyhq.com/example/{OTHER_UUID}",
                        "title": "Backend Engineer",
                    }
                ]
            },
            "closed",
        ),
        (
            {
                "jobs": [
                    {
                        "jobUrl": f"https://jobs.ashbyhq.com/example/{UUID}",
                        "title": "Backend Engineer",
                        "isListed": False,
                    }
                ]
            },
            "unlisted",
        ),
        (
            {
                "jobs": [
                    {
                        "jobUrl": f"https://jobs.ashbyhq.com/example/{UUID}",
                        "id": OTHER_UUID,
                        "title": "Backend Engineer",
                    }
                ]
            },
            "identity_mismatch",
        ),
        ({"error": "not a jobs board"}, "invalid_response"),
    ],
)
def test_ashby_missing_unlisted_and_conflicting_jobs_are_unavailable(payload, reason):
    url = f"https://jobs.ashbyhq.com/example/{UUID}"
    result = enrich_job(_job(source_url=url), fetcher=_json_fetch(payload))
    assert result.status == "unavailable"
    assert result.job is None
    assert result.reason_code == reason


@pytest.mark.parametrize(
    "updates",
    [
        {"id": 12346},
        {"title": "Frontend Engineer"},
        {"company_name": "Different Employer"},
        {"absolute_url": "https://boards.greenhouse.io/example/jobs/12346"},
        {"absolute_url": "https://boards.greenhouse.io/another/jobs/12345"},
    ],
)
def test_ats_identity_mismatch_never_enriches(updates):
    result = enrich_job(
        _job(source_url="https://boards.greenhouse.io/example/jobs/12345"),
        fetcher=_json_fetch(_gh(**updates)),
    )
    assert result.status == "unavailable"
    assert result.job is None
    assert result.reason_code == "identity_mismatch"


def test_custom_greenhouse_absolute_url_is_not_fetched_or_promoted():
    url = "https://boards.greenhouse.io/example/jobs/12345"
    requested = []
    result = enrich_job(
        _job(source_url=url),
        fetcher=_json_fetch(_gh(absolute_url="https://careers.example.test/jobs/12345"), requested),
    )
    assert result.status == "enriched"
    assert result.job.application_url == url
    assert len(requested) == 1


def test_greenhouse_escaped_html_is_real_plain_description():
    from html import escape

    result = enrich_job(
        _job(source_url="https://job-boards.greenhouse.io/example/jobs/12345"),
        fetcher=_json_fetch(_gh(content=escape(escape(f"<p>{BODY}</p>")))),
    )
    assert result.job.description == BODY


def test_greenhouse_embed_uses_explicit_board_and_job_not_guesses():
    requested = []
    result = enrich_job(
        _job(source_url="https://boards.greenhouse.io/embed/job_app?for=example&token=12345"),
        fetcher=_json_fetch(_gh(), requested),
    )
    assert result.status == "enriched"
    assert "/example/jobs/12345?content=true" in str(requested[0].url)


@pytest.mark.parametrize("provider", ["linkedin", "indeed", "lin_srael", "comeet"])
def test_known_public_boards_jobposting_schema_only(provider):
    urls = {
        "linkedin": LINKEDIN,
        "indeed": "https://il.indeed.com/viewjob?jk=abcd1234",
        "lin_srael": "https://lin-srael.com/jobs/example-id",
        "comeet": "https://www.comeet.com/jobs/example/12.345/backend-engineer/AA.123",
    }
    url = urls[provider]
    result = enrich_job(_job(source_url=url), fetcher=_page_fetch(_schema(url=url), canonical=url))
    assert result.status == "enriched"
    assert result.job.description == BODY
    assert result.job.source_url == url
    assert result.job.application_url == (url if provider == "comeet" else None)


@pytest.mark.parametrize(
    "wrap",
    [
        lambda data: data,
        lambda data: [data],
        lambda data: {"@graph": [{"@type": "Organization"}, data]},
    ],
)
def test_jsonld_roots_arrays_and_graphs(wrap):
    result = enrich_job(
        _job(), fetcher=_page_fetch(wrap(_schema(**{"@type": ["Thing", "JobPosting"]})))
    )
    assert result.status == "enriched"


def test_linkedin_identifier_matches_exact_source_id_without_url():
    posting = _schema(identifier={"@type": "PropertyValue", "value": "4478499323"})
    del posting["url"]
    result = enrich_job(_job(), fetcher=_page_fetch(posting, canonical=None))
    assert result.status == "enriched"
    assert result.job.application_url is None


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://www.linkedin.com/jobs/view/123"},
        {"title": "Frontend Engineer"},
        {"hiringOrganization": {"name": "Another Company"}},
        {"identifier": {"value": "123"}},
        {"url": "https://linkedin.com.attacker.test/jobs/view/4478499323"},
    ],
)
def test_schema_identity_mismatch(changes):
    result = enrich_job(_job(), fetcher=_page_fetch(_schema(**changes)))
    assert result.status == "unavailable"
    assert result.job is None
    assert result.reason_code == "identity_mismatch"


def test_canonical_url_must_not_silently_change_job_identity():
    result = enrich_job(
        _job(), fetcher=_page_fetch(_schema(), canonical="https://www.linkedin.com/jobs/view/123")
    )
    assert result.reason_code == "identity_mismatch"


def test_matching_schema_selects_exact_posting_not_recommended_job():
    result = enrich_job(
        _job(),
        fetcher=_page_fetch([_schema(url="https://www.linkedin.com/jobs/view/123"), _schema()]),
    )
    assert result.status == "enriched"
    ambiguous = enrich_job(_job(), fetcher=_page_fetch([_schema(), _schema()]))
    assert ambiguous.reason_code == "identity_mismatch"


def test_nested_recommendations_are_not_treated_as_the_page_posting():
    result = enrich_job(
        _job(), fetcher=_page_fetch({"@type": "WebPage", "recommendation": _schema()})
    )
    assert result.reason_code == "no_description"


def test_linkedin_public_guest_markup_requires_canonical_title_and_company():
    markup = (
        '<h1 class="top-card-layout__title">Backend Engineer</h1>'
        '<a class="topcard__org-name-link">Example Labs</a>'
        f'<div class="description__text"><div class="show-more-less-html__markup">{BODY}</div></div>'
    )
    result = enrich_job(_job(), fetcher=_page_fetch(extra=markup))
    assert result.status == "enriched"
    assert result.job.description == BODY
    assert result.job.application_url is None
    assert (
        enrich_job(_job(), fetcher=_page_fetch(extra=markup, canonical=None)).status
        == "unavailable"
    )
    assert (
        enrich_job(
            _job(), fetcher=_page_fetch(extra=markup.replace("Example Labs", "Wrong Company"))
        ).reason_code
        == "identity_mismatch"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Backend Engineer",
        "Published Lin-Srael job listing.",
        "Apply now to see job details.",
        "Backend Engineer " * 30,
        "",
        "Build Python APIs with Docker.",
    ],
)
def test_title_only_and_short_placeholders_are_not_full_descriptions(text):
    result = enrich_job(_job(), fetcher=_page_fetch(_schema(description=text)))
    assert result.status == "unavailable"
    assert result.job is None
    assert result.reason_code == "description_incomplete"
    assert "manually" in result.message


@pytest.mark.parametrize(
    "title, visible",
    [
        ("Sign In | LinkedIn", ""),
        ("Just a moment...", "Checking your browser"),
        ("Access denied", ""),
        ("Before you continue", "Accept cookies"),
        ("Backend Engineer", "Please verify you are human"),
        ("Backend Engineer", "Sign in to view this job description"),
    ],
)
def test_login_cookie_and_antibot_pages_are_gated_even_with_cached_schema(title, visible):
    result = enrich_job(_job(), fetcher=_page_fetch(_schema(), title=title, extra=visible))
    assert result.status == "unavailable"
    assert result.reason_code == "source_gated"
    assert result.job is None


def test_cookie_banner_does_not_replace_or_invalidate_real_description():
    result = enrich_job(
        _job(), fetcher=_page_fetch(_schema(), extra="We use cookies. Accept all. Sign in.")
    )
    assert result.status == "enriched"
    assert "cookies" not in result.job.description


@pytest.mark.parametrize(
    "schema, visible",
    [
        (_schema(validThrough="2000-01-01"), ""),
        (_schema(validThrough="2000-01-01T00:00:00Z"), ""),
        (_schema(), "No longer accepting applications"),
        (_schema(description="This job has expired."), ""),
    ],
)
def test_closed_and_expired_roles_are_never_enriched(schema, visible):
    result = enrich_job(_job(), fetcher=_page_fetch(schema, extra=visible))
    assert result.status == "unavailable"
    assert result.reason_code == "closed"
    assert result.job is None


@pytest.mark.parametrize(
    "status, reason",
    [
        (404, "closed"),
        (410, "closed"),
        (401, "source_gated"),
        (403, "source_gated"),
        (429, "source_gated"),
        (999, "source_gated"),
        (500, "http_error"),
    ],
)
def test_http_failures_have_honest_stable_outcomes(status, reason):
    result = enrich_job(_job(), fetcher=_fetcher(lambda request: httpx.Response(status)))
    assert result.reason_code == reason
    assert result.job is None
    assert result.status == "unavailable"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.linkedin.com/jobs/view/123",
        "https://www.linkedin.com:444/jobs/view/123",
        "https://user:password@www.linkedin.com/jobs/view/123",
        "https://user@www.linkedin.com/jobs/view/123",
        "https://www.linkedin.com/jobs/view/123?access_token=private",
        "https://www.linkedin.com/jobs/view/123?password=private",
        "https://www.linkedin.com/jobs/view/123?code=private",
        "https://www.linkedin.com/jobs/view/123?api_key=private",
        "https://www.linkedin.com/jobs/view/123#private",
        "https://www.linkedin.com/jobs/view/123?url=https%3A%2F%2F127.0.0.1",
        "https://www.linkedin.com/jobs/view/123\n",
        "https://www.linkedin.com\\@127.0.0.1/jobs/view/123",
        "https://www.linkedin.com/jobs%2fview/123",
        "https://www.linkedin.com:invalid/jobs/view/123",
    ],
)
def test_unsafe_urls_never_reach_fetcher_or_evidence(url):
    def forbidden(_):
        pytest.fail("Unsafe URL reached HTTP")

    result = enrich_job(_job().model_copy(update={"source_url": url}), fetcher=forbidden)
    assert result.reason_code == "unsafe_url"
    assert result.evidence_urls == ()
    assert result.job is None
    assert "private" not in result.message


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/jobs/123",
        "https://[::1]/jobs/123",
        "https://169.254.169.254/latest/meta-data",
        "https://linkedin.com.evil.test/jobs/view/123",
        "https://notlinkedin.com/jobs/view/123",
        "https://api.eu.lever.co.evil.test/v0/postings/example/123",
        "https://careers.example.test/jobs/123",
        "https://jobs.lever.co/example",
        "https://www.linkedin.com/jobs/search",
        "https://il.indeed.com/",
        "https://api.lever.co/v0/postings/example/123/apply",
        "https://api.ashbyhq.com/jobPosting.list",
    ],
)
def test_unapproved_hosts_endpoints_and_roots_never_reach_fetcher(url):
    result = enrich_job(
        _job(source_url=url), fetcher=lambda _: pytest.fail("Unsupported URL reached HTTP")
    )
    assert result.reason_code == "unsupported_url"
    assert result.evidence_urls == ()


@pytest.mark.parametrize(
    "destination, reason",
    [
        ("https://127.0.0.1/internal", "unsupported_url"),
        ("https://www.linkedin.com/login", "source_gated"),
        ("https://www.linkedin.com/jobs/view/123", "redirect_blocked"),
        (f"https://jobs.lever.co/example/{UUID}", "redirect_blocked"),
        ("https://www.linkedin.com:8443/jobs/view/4478499323", "unsafe_url"),
        ("https://user:private@www.linkedin.com/jobs/view/4478499323", "unsafe_url"),
    ],
)
def test_redirects_cannot_leave_the_exact_trusted_posting(destination, reason):
    requested = []

    def handle(request):
        requested.append(request)
        return httpx.Response(302, headers={"location": destination})

    result = enrich_job(_job(), fetcher=_fetcher(handle))
    assert result.status == "unavailable"
    assert result.reason_code == reason
    assert len(requested) == 1
    assert len(result.evidence_urls) == 1


def test_redirect_to_same_linkedin_job_on_trusted_locale_host_is_allowed():
    requested = []

    def handle(request):
        requested.append(request)
        if request.url.host == "www.linkedin.com":
            return httpx.Response(
                302, headers={"location": "https://il.linkedin.com/jobs/view/4478499323"}
            )
        return httpx.Response(200, text=_html(_schema()), headers={"content-type": "text/html"})

    result = enrich_job(_job(), fetcher=_fetcher(handle))
    assert result.status == "enriched"
    assert len(requested) == 2
    assert result.job.source_url == LINKEDIN


def test_redirect_loops_stop_at_fixed_limit():
    requested = []

    def handle(request):
        requested.append(request)
        host = "il.linkedin.com" if request.url.host == "www.linkedin.com" else "www.linkedin.com"
        return httpx.Response(302, headers={"location": f"https://{host}/jobs/view/4478499323"})

    result = enrich_job(_job(), fetcher=_fetcher(handle))
    assert result.reason_code == "redirect_blocked"
    assert len(requested) == enrichment.MAX_REDIRECTS + 1


def test_unrestricted_autofollowing_injected_fetcher_is_rejected():
    history = [httpx.Response(302, request=httpx.Request("GET", LINKEDIN))]
    result = enrich_job(
        _job(), fetcher=lambda _: httpx.Response(200, text=_html(_schema()), history=history)
    )
    assert result.reason_code == "redirect_blocked"


def test_closed_official_posting_can_fall_back_to_matching_public_source():
    requested = []

    def handle(request):
        requested.append(request)
        if request.url.host == "api.eu.lever.co":
            return httpx.Response(404)
        return httpx.Response(200, text=_html(_schema()), headers={"content-type": "text/html"})

    result = enrich_job(_job(application_url=LEVER_EU), fetcher=_fetcher(handle))
    assert result.status == "enriched"
    assert [request.url.host for request in requested] == ["api.eu.lever.co", "www.linkedin.com"]
    assert result.job.application_url is None


def test_skills_and_years_come_only_from_body_not_title_or_existing_fields():
    job = _job(title="Java Backend Engineer")
    result = enrich_job(job, fetcher=_page_fetch(_schema(title=job.title)))
    assert result.status == "enriched"
    assert "Java" not in result.job.required_skills
    assert result.job.years_experience == "3+ years"
    no_years = enrich_job(
        job,
        fetcher=_page_fetch(
            _schema(
                title=job.title,
                description=BODY.replace("3+ years of experience", "practical experience"),
            )
        ),
    )
    assert no_years.job.years_experience is None


@pytest.mark.parametrize("declared_length", [None, enrichment.MAX_BODY_BYTES + 1])
def test_body_is_bounded_with_or_without_content_length(declared_length):
    headers = {"content-type": "text/html"}
    if declared_length:
        headers["content-length"] = str(declared_length)
    result = enrich_job(
        _job(),
        fetcher=_fetcher(
            lambda request: httpx.Response(
                200,
                content=b"x" * (enrichment.MAX_BODY_BYTES + 1),
                headers=headers,
            )
        ),
    )
    assert result.reason_code == "response_too_large"
    assert result.job is None


def test_compressed_decoded_body_is_bounded():
    compressed = gzip.compress(b"x" * (enrichment.MAX_BODY_BYTES + 1))
    result = enrich_job(
        _job(),
        fetcher=_fetcher(
            lambda request: httpx.Response(
                200,
                content=compressed,
                headers={"content-type": "text/html", "content-encoding": "gzip"},
            )
        ),
    )
    assert result.reason_code == "response_too_large"


def test_description_length_is_bounded_without_truncation():
    result = enrich_job(_job(), fetcher=_page_fetch(_schema(description=BODY * 100)))
    assert result.reason_code == "description_too_large"


def test_timeout_is_unavailable_and_does_not_expose_exception_credentials():
    def handle(request):
        raise httpx.ReadTimeout("private credential material", request=request)

    result = enrich_job(_job(), fetcher=_fetcher(handle))
    assert result.reason_code == "fetch_timeout"
    assert result.job is None
    assert "credential" not in result.message


def test_total_deadline_stops_reading(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(enrichment.time, "monotonic", lambda: clock[0])

    def handle(request):
        clock[0] = enrichment.TOTAL_SECONDS + 1
        return httpx.Response(200, text=_html(_schema()), headers={"content-type": "text/html"})

    result = enrich_job(_job(), fetcher=_fetcher(handle))
    assert result.reason_code == "fetch_timeout"


def test_request_budget_is_shared_across_official_and_source(monkeypatch):
    monkeypatch.setattr(enrichment, "MAX_REQUESTS", 1)
    requested = []

    def handle(request):
        requested.append(request)
        return httpx.Response(404)

    result = enrich_job(_job(application_url=LEVER_EU), fetcher=_fetcher(handle))
    assert result.reason_code == "request_limit"
    assert len(requested) == 1


@pytest.mark.parametrize(
    "body, content_type",
    [
        (b"invalid json", "application/json"),
        (b"{}", "application/json"),
        (b"<html>Sign in</html>", "text/html"),
    ],
)
def test_invalid_ats_response_is_not_used_as_description(body, content_type):
    result = enrich_job(
        _job(source_url="https://boards.greenhouse.io/example/jobs/12345"),
        fetcher=_fetcher(
            lambda request: httpx.Response(
                200,
                content=body,
                headers={"content-type": content_type},
            )
        ),
    )
    assert result.status == "unavailable"
    assert result.job is None


def test_no_schema_or_guest_body_means_manual_fallback_even_if_page_is_long():
    result = enrich_job(_job(), fetcher=_page_fetch(extra=f"<main>{BODY}</main>"))
    assert result.reason_code == "no_description"
    assert "manually" in result.message


def test_malformed_jsonld_does_not_crash_or_trigger_more_requests():
    result = enrich_job(
        _job(), fetcher=_page_fetch(extra='<script type="application/ld+json">{bad}</script>')
    )
    assert result.reason_code == "no_description"
    assert len(result.evidence_urls) == 1


def test_full_public_source_preserves_previously_verified_custom_employer_route():
    official = "https://employer.example.test/careers/backend/apply"
    result = enrich_job(_job(application_url=official), fetcher=_page_fetch(_schema()))
    assert result.status == "enriched"
    assert result.job.application_url == official


def test_custom_employer_route_with_secret_query_is_not_retained():
    official = "https://employer.example.test/careers/backend?api_key=private"
    result = enrich_job(_job(application_url=official), fetcher=_page_fetch(_schema()))
    assert result.status == "enriched"
    assert result.job.application_url is None


def test_default_transport_is_streaming_read_only_and_ignores_environment(monkeypatch):
    real_client = httpx.Client
    options = {}
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text=_html(_schema()), headers={"content-type": "text/html"})

    def client_factory(**kwargs):
        options.update(kwargs)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(enrichment.httpx, "Client", client_factory)
    result = enrich_job(_job())
    assert result.status == "enriched"
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert options["timeout"] == enrichment.TIMEOUT_SECONDS
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert not requests[0].content
    assert "authorization" not in requests[0].headers
    assert "cookie" not in requests[0].headers
