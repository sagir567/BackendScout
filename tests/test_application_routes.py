"""Public synthetic HTML only; no browser, accounts, or real DNS/network calls."""

import json
import re
import socket
from datetime import datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace
from urllib.parse import urljoin

import pytest
from bs4 import BeautifulSoup

from backend_scout import application_routes as routes
from backend_scout.application_routes import (
    ApplicationRoute,
    resolve_application_route,
    route_allows_url,
    safe_public_url,
)
from backend_scout.models import Job

EMPLOYER = "https://acme.test/jobs/backend"
ATS = "https://job-boards.greenhouse.io/acme/jobs/123"
COMEET = "https://www.comeet.co/jobs-embed/acme/12.345/AB.123?token=public-widget"
FORM = '<form id="application"><label>Resume<input type="file" name="resume"></label><button>Apply</button></form>'


def html(extra="", *, company="Acme", title="Backend Engineer"):
    return f"<html><body><header>{company}</header><h1>{title}</h1>{extra}</body></html>"


def posting(*, company="Acme", title="Backend Engineer", identifier="123"):
    value = {"@type": "JobPosting", "hiringOrganization": {"name": company}, "title": title}
    if identifier is not None:
        value["identifier"] = {"@type": "PropertyValue", "value": identifier}
    return '<script type="application/ld+json">' + json.dumps(value) + "</script>"


def job(source=EMPLOYER, application=None):
    return Job(
        source="fixture",
        source_url=source,
        application_url=application,
        company="Acme",
        title="Backend Engineer",
        description="Synthetic public role.",
    )


def snapshot(source):
    """Parser-backed stand-in for the browser's visible DOM snapshot."""
    soup = BeautifulSoup(source, "html.parser")
    schemas = [tag.get_text() for tag in soup.select('script[type="application/ld+json"]')]
    for tag in list(soup.select("script, [hidden], [aria-hidden=true], [style]")):
        if (
            tag.name == "script"
            or tag.has_attr("hidden")
            or tag.get("aria-hidden") == "true"
            or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", tag.get("style", ""))
        ):
            tag.decompose()
    forms = []
    for form in soup.select("form"):
        files = form.select('input[type="file"]:not([disabled])')
        forms.append(
            {
                "action": form.get("action", ""),
                "application": bool(
                    re.search(
                        r"\b(apply|application)\b",
                        form.get_text() + " " + form.get("id", ""),
                        re.IGNORECASE,
                    )
                ),
                "resume": sum(
                    bool(
                        re.search(
                            r"resume|\bcv\b|\.pdf|\.doc|application/pdf",
                            " ".join(
                                str(item.get(key, ""))
                                for key in ("name", "id", "accept", "aria-label")
                            )
                            + " "
                            + (item.parent.get_text() if item.parent.name == "label" else ""),
                            re.IGNORECASE,
                        )
                    )
                    for item in files
                )
                == 1,
                "ids": [
                    item.get("value", "")
                    for item in form.select('input[type="hidden"]')
                    if re.fullmatch(
                        r"job_?id|position_?id|position_uid|gh_jid",
                        item.get("name", ""),
                        re.IGNORECASE,
                    )
                ],
            }
        )
    return {
        "body": soup.get_text("\n", strip=True),
        "headings": [tag.get_text() for tag in soup.select("h1,h2,h3")],
        "schemas": schemas,
        "content": [
            tag.get_text("\n", strip=True)
            for tag in soup.select("main,article")
            if not tag.find_parent(["main", "article"])
        ],
        "links": [
            {
                "url": tag["href"],
                "label": " ".join(
                    (tag.get_text(), tag.get("aria-label", ""), tag.get("title", ""))
                ),
            }
            for tag in soup.select("a[href]")
        ],
        "frames": [tag["src"] for tag in soup.select("iframe[src]")],
        "forms": forms,
    }


class FakeIntercept:
    def __init__(self, page, url, method="GET", *, main=True):
        self.request = SimpleNamespace(
            url=url,
            method=method,
            frame=page.main_frame if main else object(),
            is_navigation_request=lambda: main,
        )
        self.allowed = None

    def continue_(self):
        self.allowed = True

    def abort(self):
        self.allowed = False


class FakePage:
    def __init__(self, pages, *, redirects=None, requests=()):
        self.pages = pages
        self.redirects = redirects or {}
        self.requests = requests
        self.main_frame = object()
        self.url = "about:blank"
        self.visited = []
        self.interceptions = []
        self.handler = None
        self.removed = False

    def route(self, pattern, handler):
        assert pattern == "**/*"
        self.handler = handler

    def unroute(self, pattern, handler):
        assert handler is self.handler
        self.handler = None
        self.removed = True

    def request(self, url, method="GET", *, main=True):
        intercepted = FakeIntercept(self, url, method, main=main)
        self.handler(intercepted)
        self.interceptions.append(intercepted)
        return intercepted.allowed

    def goto(self, url, **kwargs):
        assert kwargs == {"wait_until": "domcontentloaded", "timeout": 20_000}
        for _ in range(8):
            if not self.request(url):
                raise RuntimeError("Blocked navigation URL must never be echoed: ?password=private")
            self.visited.append(url)
            if url not in self.redirects:
                break
            url = urljoin(url, self.redirects[url])
        else:
            raise AssertionError("Unexpected unbounded redirects")
        self.url = url
        for request in self.requests:
            self.request(**request)
        source = self.pages[url]
        return SimpleNamespace(status=source if isinstance(source, int) else 200)

    def evaluate(self, script):
        assert "document.body.innerText" in script
        assert ".value" not in script.split("ids:")[0]
        return snapshot(self.pages[self.url])


@pytest.fixture(autouse=True)
def isolated_dns(monkeypatch):
    def lookup(host, port, **kwargs):
        if host == "dns-localhost.acme.com":
            address = "127.0.0.1"
        elif host == "mixed.acme.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ("8.8.8.8", "10.0.0.1")
            ]
        elif host == "missing.acme.com":
            raise socket.gaierror("fixture DNS failure")
        else:
            address = "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(routes.socket, "getaddrinfo", lookup)


def test_official_employer_form_and_contract():
    page = FakePage({EMPLOYER: html(FORM)})
    route = resolve_application_route(job(), page)
    assert route.status == "verified"
    assert route.source_url == route.employer_posting_url == route.application_url == EMPLOYER
    assert route.adapter_name == "employer-form"
    assert route.allowed_origins == ["https://acme.test"]
    assert route.evidence_urls == [EMPLOYER]
    assert datetime.fromisoformat(route.checked_at).utcoffset() == timedelta(0)
    assert page.removed
    assert set(route.model_dump()) == {
        "status",
        "reason_code",
        "message",
        "source_url",
        "employer_posting_url",
        "application_url",
        "company",
        "title",
        "job_id",
        "adapter_name",
        "checked_at",
        "evidence_urls",
        "allowed_origins",
        "posting_text",
    }


def test_operator_verified_brand_alias_is_exact_host_scoped_and_still_checks_identity():
    url = "https://acmesecurity.test/jobs/backend"
    assert resolve_application_route(job(url), FakePage({url: html(FORM)})).status == "needs_input"
    assert resolve_application_route(job(url), FakePage({url: html(FORM)}),
                                     verified_employer_hosts=("acmesecurity.test",)).status == "verified"
    assert resolve_application_route(job(url), FakePage({url: html(FORM, company="Another")} ),
                                     verified_employer_hosts=("acmesecurity.test",)).status == "needs_input"


def test_observed_employer_apply_anchor_to_known_ats():
    page = FakePage({EMPLOYER: html(f'<a href="{ATS}">Apply now</a>'), ATS: html(FORM + posting())})
    route = resolve_application_route(job(), page)
    assert route.status == "verified"
    assert route.application_url == ATS
    assert route.employer_posting_url == EMPLOYER
    assert route.job_id == "123"
    assert route.adapter_name == "greenhouse"
    assert route.allowed_origins == ["https://acme.test", "https://job-boards.greenhouse.io"]


@pytest.mark.parametrize("host", ["www.comeet.co", "www.comeet.com"])
def test_public_comeet_widget_only_from_verified_employer(host):
    widget = COMEET.replace("www.comeet.co/", host + "/")
    page = FakePage(
        {
            EMPLOYER: html(f'<iframe src="{widget}"></iframe>'),
            widget: html(FORM + posting(identifier="AB.123")),
        }
    )
    route = resolve_application_route(job(), page)
    assert route.status == "verified"
    assert route.adapter_name == "comeet"
    assert route.job_id == "ab.123"
    assert route.application_url == widget
    assert route_allows_url(route, widget)
    assert not route_allows_url(
        route, widget.replace("public-widget", "another-token"), confirmation=True
    )


@pytest.mark.parametrize(
    "source", ["https://www.linkedin.com/jobs/view/5", "https://www.indeed.com/viewjob?jk=5"]
)
def test_discovery_board_link_out_is_not_a_delivery_destination(source):
    page = FakePage(
        {source: html(f'<a href="{EMPLOYER}">Apply on company website</a>'), EMPLOYER: html(FORM)}
    )
    route = resolve_application_route(job(source), page)
    assert route.status == "verified"
    assert route.source_url == source
    assert route.application_url == EMPLOYER
    assert route_allows_url(route, source, confirmation=True) is False
    board_only = resolve_application_route(job(source), FakePage({source: html(FORM)}))
    assert board_only.status == "needs_input"
    assert board_only.application_url is None


@pytest.mark.parametrize(
    "explicit",
    [
        "http://acme.test/apply",
        "https://www.linkedin.com/jobs/view/5",
        "https://acme.test/login",
        "https://acme.test/apply?token=secret",
    ],
)
def test_unsafe_or_discovery_explicit_link_falls_back_without_rewriting_source(explicit):
    page = FakePage({EMPLOYER: html(FORM)})
    route = resolve_application_route(job(application=explicit), page)
    assert route.status == "verified"
    assert route.source_url == EMPLOYER
    assert page.visited == [EMPLOYER]


def test_safe_explicit_application_is_preferred():
    page = FakePage({ATS: html(FORM)})
    source = "https://www.linkedin.com/jobs/view/5"
    route = resolve_application_route(job(source, ATS), page)
    assert route.status == "verified"
    assert route.source_url == source
    assert page.visited == [ATS]


@pytest.mark.parametrize(
    "source,code",
    [
        (html(FORM, company="Other"), "company_mismatch"),
        (html(FORM, title="Senior Backend Engineer"), "title_mismatch"),
        (html(FORM + posting(company="Other")), "company_mismatch"),
        (html(FORM + posting(title="Backend Developer")), "title_mismatch"),
        (html(FORM + posting(identifier="456")), "job_id_mismatch"),
        (
            html(FORM.replace("</form>", '<input type="hidden" name="job_id" value="456"></form>')),
            "job_id_mismatch",
        ),
        (html(FORM + posting() + posting()), "ambiguous_identity"),
    ],
)
def test_exact_identity_mismatch_fails_closed(source, code):
    route = resolve_application_route(job(ATS), FakePage({ATS: source}))
    assert route.status == "needs_input"
    assert route.reason_code == code
    assert route.application_url is None
    assert route.allowed_origins == []


def test_identity_uses_normalization_and_exact_jsonld_graph():
    schema = {
        "@graph": [
            {
                "@type": ["Thing", "JobPosting"],
                "title": "BACKEND  Engineer",
                "hiringOrganization": {"name": "ACME"},
                "identifier": {"value": "123"},
            }
        ]
    }
    source = (
        '<body><script type="application/ld+json">'
        + json.dumps(schema)
        + "</script>"
        + FORM
        + "</body>"
    )
    route = resolve_application_route(job(ATS), FakePage({ATS: source}))
    assert route.status == "verified"


def test_hidden_title_is_not_identity_evidence():
    source = html(FORM, title="Other Role") + "<h1 hidden>Backend Engineer</h1>"
    route = resolve_application_route(job(), FakePage({EMPLOYER: source}))
    assert route.reason_code == "title_mismatch"


@pytest.mark.parametrize(
    "url",
    [
        "https://job-boards.greenhouse.io.evil.test/acme/jobs/123",
        "https://acme.evil.test/jobs/backend",
        "https://acme.com.test/jobs/backend",
        "https://acme.com.dev/jobs/backend",
        "https://comeet.co.evil.test/jobs/acme/123/AB.123",
    ],
)
def test_deceptive_hosts_do_not_gain_employer_authority(url):
    route = resolve_application_route(job(url), FakePage({url: html(FORM)}))
    assert route.status == "needs_input"
    assert route.reason_code == "untrusted_employer"


@pytest.mark.parametrize(
    "extra,code",
    [
        ('<a href="/apply/one">Apply</a><a href="/apply/two">Apply</a>', "ambiguous_apply_links"),
        (FORM + FORM, "ambiguous_forms"),
        (FORM + '<iframe src="https://untrusted.test/form"></iframe>', "ambiguous_forms"),
        (
            FORM.replace(
                '<form id="application">',
                '<form id="application" action="https://untrusted.test/submit">',
            ),
            "untrusted_form_action",
        ),
        ('<form id="application"><button>Apply</button></form>', "application_context_missing"),
        ('<form><input name="resume" type="file"></form>', "application_context_missing"),
    ],
)
def test_ambiguous_or_untrusted_forms_and_links(extra, code):
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(extra)}))
    assert route.status == "needs_input"
    assert route.reason_code == code


@pytest.mark.parametrize(
    "url",
    [
        "http://acme.test/jobs/123",
        "file:///etc/passwd",
        "https://user:secret@acme.test/jobs/123",
        "https://127.0.0.1/jobs/123",
        "https://8.8.8.8/jobs/123",
        "https://8.8/jobs/123",
        "https://0x7f.1/jobs/123",
        "https://[::1]/jobs/123",
        "https://localhost/jobs/123",
        "https://foo.localhost/jobs/123",
        "https://foo.internal/jobs/123",
        "https://dns-localhost.acme.com/jobs/123",
        "https://mixed.acme.com/jobs/123",
        "https://missing.acme.com/jobs/123",
        "https://acme.test:8443/jobs/123",
        "https://acme.test/login",
        "https://acme.test/%256cogin",
        "https://acme.test/oauth/authorize",
        "https://acme.test/jobs/123?access_token=secret",
        "https://acme.test/jobs/123?code=secret",
        "https://acme.test/jobs/123?password=secret",
        "https://acme.test/jobs/123?redirect=https://evil.test",
        "https://[invalid",
        "https://acme.test\\@evil.test/jobs/123",
    ],
)
def test_unsafe_public_urls_never_navigate_or_echo_secrets(url):
    page = FakePage({})
    route = resolve_application_route(job(url), page)
    assert route.status == "needs_input"
    assert route.reason_code == "unsafe_url"
    assert route.source_url == url
    assert "secret" not in route.message
    assert page.visited == []
    assert route.evidence_urls == []
    assert not safe_public_url(url, tracker="test")


def test_test_domains_require_explicit_test_tracker():
    assert (
        resolve_application_route(job(), FakePage({EMPLOYER: html(FORM)}), tracker="test").status
        == "verified"
    )
    page = FakePage({})
    route = resolve_application_route(job(), page, tracker="production")
    assert route.reason_code == "unsafe_url"
    assert page.visited == []


def test_public_production_domain_works_with_offline_dns():
    url = "https://careers.acme.com/jobs/backend"
    route = resolve_application_route(job(url), FakePage({url: html(FORM)}), tracker="production")
    assert route.status == "verified"
    assert route_allows_url(route, url)


def test_read_only_guard_blocks_mutations_private_requests_and_redirects():
    page = FakePage(
        {EMPLOYER: html(FORM)},
        requests=[
            {"url": "https://acme.test/api/apply", "method": "POST", "main": False},
            {"url": "https://dns-localhost.acme.com/internal", "main": False},
            {"url": "https://acme.test/logo.png", "main": False},
        ],
    )
    assert resolve_application_route(job(), page).status == "verified"
    assert [item.allowed for item in page.interceptions] == [True, False, False, True]
    page = FakePage({}, redirects={EMPLOYER: "https://dns-localhost.acme.com/jobs/123"})
    route = resolve_application_route(job(), page)
    assert route.reason_code == "unsafe_redirect"
    assert "private" not in route.message
    assert page.visited == [EMPLOYER]
    assert page.removed


def test_unobserved_cross_origin_redirect_rejected_even_to_known_ats():
    page = FakePage({}, redirects={EMPLOYER: ATS})
    route = resolve_application_route(job(), page)
    assert route.reason_code == "unsafe_redirect"
    assert page.visited == [EMPLOYER]


def test_three_observed_hops_and_redirect_budget_are_bounded():
    urls = [EMPLOYER, *[f"https://acme.test/apply/{i}" for i in range(1, 5)]]
    pages = {url: html(f'<a href="{urls[i + 1]}">Apply</a>') for i, url in enumerate(urls[:-1])}
    pages[urls[-1]] = html(FORM)
    page = FakePage(pages)
    assert resolve_application_route(job(), page).reason_code == "hop_limit"
    assert page.visited == urls[:4]
    pages[urls[3]] = html(FORM)
    page = FakePage(pages)
    assert resolve_application_route(job(), page).status == "verified"
    redirects = dict(pairwise(urls))
    page = FakePage(pages, redirects=redirects)
    assert resolve_application_route(job(), page).reason_code == "unsafe_redirect"
    assert len(page.visited) == 4


@pytest.mark.parametrize("url", [COMEET.split("?", 1)[0], COMEET])
def test_direct_embed_has_no_token_exception_or_employer_authority(url):
    page = FakePage({url: html(FORM + posting(identifier="AB.123"))})
    route = resolve_application_route(job(url), page)
    assert route.status == "needs_input"
    assert route.reason_code in {"unsafe_url", "unbound_widget"}


def test_widget_rejects_other_credentials_and_mismatched_employer_job_binding():
    widget = COMEET + "&access_token=secret"
    page = FakePage({EMPLOYER: html(f'<iframe src="{widget}"></iframe>')})
    assert resolve_application_route(job(), page).reason_code == "unsafe_apply_link"
    source = EMPLOYER + "?position_uid=ZZ.999"
    page = FakePage({source: html(f'<iframe src="{COMEET}"></iframe>')})
    assert resolve_application_route(job(source), page).reason_code == "job_id_mismatch"


def test_wrong_job_in_apply_anchor_or_explicit_link():
    other = ATS.replace("123", "456")
    page = FakePage({ATS: html(f'<a href="{other}/apply">Apply</a>')})
    assert resolve_application_route(job(ATS), page).reason_code == "job_id_mismatch"
    page = FakePage({})
    assert resolve_application_route(job(ATS, other), page).reason_code == "job_id_mismatch"
    assert page.visited == []


def test_unknown_is_not_claimed_closed_and_observed_closure_is_distinct():
    route = resolve_application_route(job(), FakePage({EMPLOYER: html()}))
    assert route.status == "needs_input"
    assert route.reason_code == "application_route_missing"
    route = resolve_application_route(job(), FakePage({EMPLOYER: html("This position is closed.")}))
    assert (route.status, route.reason_code) == ("unavailable", "vacancy_closed")
    route = resolve_application_route(job(), FakePage({EMPLOYER: 404}))
    assert (route.status, route.reason_code) == ("unavailable", "posting_unavailable")
    route = resolve_application_route(job(), FakePage({EMPLOYER: 403}))
    assert route.status == "needs_input"


def test_route_scope_requires_exact_origin_job_and_explicit_confirmation_mode():
    route = resolve_application_route(job(ATS), FakePage({ATS: html(FORM)}))
    assert route_allows_url(route, ATS)
    for url in (
        ATS.replace("123", "456"),
        ATS.replace("https:", "http:"),
        "https://evil.test/receipt",
        "https://another.greenhouse.io/acme/jobs/123",
        "https://job-boards.greenhouse.io/other/jobs/123",
        "https://job-boards.greenhouse.io/login",
    ):
        assert not route_allows_url(route, url, confirmation=True)
    endpoint = "https://job-boards.greenhouse.io/api/applications"
    assert not route_allows_url(route, endpoint)
    assert route_allows_url(route, endpoint, confirmation=True)
    receipt = "https://job-boards.greenhouse.io/thank-you"
    assert not route_allows_url(route, receipt)
    assert route_allows_url(route, receipt, confirmation=True)
    route.status = "needs_input"
    assert not route_allows_url(route, ATS, confirmation=True)


def test_generic_custom_ajax_does_not_authorize_another_job_or_origin():
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(FORM)}))
    endpoint = "https://acme.test/wp-admin/admin-ajax.php?action=public_application"
    assert not route_allows_url(route, endpoint)
    assert route_allows_url(route, endpoint, confirmation=True)
    assert not route_allows_url(route, "https://acme.test/jobs/999", confirmation=True)
    assert not route_allows_url(route, "https://sub.acme.test/thank-you", confirmation=True)
    # Deserializing untrusted data does not opt into fixture hosts or widget tokens.
    assert not route_allows_url(ApplicationRoute.model_validate(route.model_dump()), EMPLOYER)


def test_safe_public_entry_defaults_to_production_and_never_grants_widget_tokens():
    assert not safe_public_url(EMPLOYER)
    assert safe_public_url(EMPLOYER, tracker="test")
    assert safe_public_url(ATS)
    assert not safe_public_url(COMEET, tracker="test")
    assert not safe_public_url("https://acme.test/submit?%2574oken=secret", tracker="test")
    assert not safe_public_url(ATS + "?job_id=456")
    assert not safe_public_url(ATS + "?job_id=123&job_id=456")
    assert not safe_public_url("https://acme.test/jobs/%2e%2e/another", tracker="test")


def test_verified_jsonld_description_is_plain_text_bounded_and_kept_across_hops():
    schema = {
        "@type": "JobPosting",
        "title": "Backend Engineer",
        "hiringOrganization": {"name": "Acme"},
        "description": "<h2>Requirements</h2><p>Python APIs &amp; tests.</p>" + "x" * 31_000,
    }
    source = html(
        '<script type="application/ld+json">'
        + json.dumps(schema)
        + "</script>"
        + f'<a href="{ATS}">Apply</a>'
    )
    route = resolve_application_route(job(), FakePage({EMPLOYER: source, ATS: html(FORM)}))
    assert route.status == "verified"
    assert route.posting_text.startswith("Requirements\nPython APIs & tests.")
    assert len(route.posting_text) == 30_000
    assert "<h2>" not in route.posting_text


@pytest.mark.parametrize("tag", ["main", "article"])
def test_identified_main_article_text_only_not_surrounding_navigation(tag):
    source = f"<body><nav>Acme unrelated navigation</nav><{tag}><h1>Backend Engineer</h1>"
    source += f"<p>Requirements: Python and SQL.</p></{tag}>" + FORM + "</body>"
    route = resolve_application_route(job(), FakePage({EMPLOYER: source}))
    assert route.status == "verified"
    assert route.posting_text == "Backend Engineer\nRequirements: Python and SQL."


@pytest.mark.parametrize(
    "extra",
    [
        "<div>Requirements: outside an identified job region.</div>",
        "<article><h2>Unrelated</h2>Unrelated text.</article>",
        "<main><h1>Backend Engineer</h1>First</main><article><h1>Backend Engineer</h1>Second</article>",
    ],
)
def test_posting_text_never_uses_unidentified_or_ambiguous_body(extra):
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(extra + FORM)}))
    assert route.status == "verified"
    assert route.posting_text is None


def test_mismatched_company_title_and_id_cannot_supply_description():
    source = html(
        FORM
        + posting(company="Other").replace(
            '"@type": "JobPosting"',
            '"@type": "JobPosting", "description": "Must not escape identity checks"',
        )
    )
    route = resolve_application_route(job(ATS), FakePage({ATS: source}))
    assert route.status == "needs_input"
    assert route.posting_text is None
    source = html("<main><h1>Wrong Role</h1>Requirements</main>", title="Wrong Role")
    route = resolve_application_route(job(), FakePage({EMPLOYER: source}))
    assert route.posting_text is None


def test_more_than_one_resume_file_is_not_a_unique_application_context():
    source = html(FORM.replace("</form>", '<input name="cv" type="file"></form>'))
    route = resolve_application_route(job(), FakePage({EMPLOYER: source}))
    assert route.status == "needs_input"
    assert route.reason_code == "application_context_missing"


def test_persisted_public_widget_keeps_only_observed_employer_derived_authority():
    employer = EMPLOYER.replace("acme.test", "acme.com")
    page = FakePage(
        {
            employer: html(f'<iframe src="{COMEET}"></iframe>'),
            COMEET: html(FORM + posting(identifier="AB.123")),
        }
    )
    resolved = resolve_application_route(job(employer), page, tracker="production")
    restored = ApplicationRoute.model_validate_json(resolved.model_dump_json())
    assert route_allows_url(restored, COMEET)
    assert not route_allows_url(
        restored, COMEET.replace("public-widget", "other"), confirmation=True
    )
    restored.employer_posting_url = None
    assert not route_allows_url(restored, COMEET)


@pytest.mark.parametrize(
    "url", ["https://[invalid", "javascript:alert(1)", "https://acme.test/login"]
)
def test_route_helper_is_a_boolean_for_malformed_or_gated_urls(url):
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(FORM)}))
    assert not route_allows_url(route, url, confirmation=True)
    route.employer_posting_url = "https://[invalid"
    route.evidence_urls.append("https://[invalid")
    assert route_allows_url(route, EMPLOYER)


def test_source_is_preserved_even_when_invalid_and_safe_explicit_entry_works():
    route = resolve_application_route(job("https://[invalid", ATS), FakePage({ATS: html(FORM)}))
    assert route.status == "verified"
    assert route.source_url == "https://[invalid"


def test_direct_official_comeet_posting_apply_link_keeps_job_binding():
    posting_url = "https://www.comeet.com/jobs/acme/12.345/backend-engineer/AB.123"
    application = posting_url + "/apply"
    page = FakePage(
        {
            posting_url: html(f'<a href="{application}">Apply</a>'),
            application: html(FORM + posting(identifier="AB.123")),
        }
    )
    route = resolve_application_route(job(posting_url), page)
    assert route.status == "verified"
    assert route.job_id == "ab.123"
    assert route.adapter_name == "comeet"


@pytest.mark.parametrize(
    "field,value", [("company", "Other"), ("title", "Wrong Role"), ("identifier", "456")]
)
def test_no_description_can_bypass_identity_or_job_binding(field, value):
    source = posting(**{field: value}).replace(
        '"@type": "JobPosting"', '"@type": "JobPosting", "description": "Must remain gated"'
    )
    route = resolve_application_route(job(ATS), FakePage({ATS: html(source + FORM)}))
    assert route.status == "needs_input"
    assert route.posting_text is None


def test_model_timestamp_is_always_utc_iso():
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(FORM)}))
    data = route.model_dump()
    data["checked_at"] = "2026-11-04T12:00:00+02:00"
    assert ApplicationRoute.model_validate(data).checked_at == "2026-11-04T10:00:00+00:00"


def test_employer_jsonld_job_id_binds_embedded_widget_even_without_id_in_employer_url():
    source = html(posting(identifier="ZZ.999") + f'<iframe src="{COMEET}"></iframe>')
    page = FakePage({EMPLOYER: source})
    route = resolve_application_route(job(), page)
    assert route.status == "needs_input"
    assert route.reason_code == "job_id_mismatch"
    assert page.visited == [EMPLOYER]


def test_allowed_requests_fall_back_to_existing_enforcement_and_fixture_handlers():
    class LayeredPage(FakePage):
        def request(self, url, method="GET", *, main=True):
            intercepted = FakeIntercept(self, url, method, main=main)
            intercepted.fallback = lambda: setattr(intercepted, "allowed", True)
            intercepted.continue_ = lambda: pytest.fail("Bypassed an earlier request handler")
            self.handler(intercepted)
            self.interceptions.append(intercepted)
            return intercepted.allowed

    page = LayeredPage({EMPLOYER: html(FORM)})
    assert resolve_application_route(job(), page).status == "verified"


@pytest.mark.parametrize(
    "iframe",
    [
        "https://www.google.com/recaptcha/api2/anchor?k=public-site-key",
        "https://google.com/recaptcha/enterprise/anchor",
        "https://www.recaptcha.net/recaptcha/api2/bframe",
        "https://recaptcha.net/recaptcha/api2/anchor",
        "https://hcaptcha.com/captcha/v1/static/hcaptcha.html",
        "https://newassets.hcaptcha.com/captcha/v1/fixture/static/hcaptcha.html#frame=checkbox",
        "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/fixture",
    ],
)
def test_verification_iframes_are_not_apply_candidates_or_granted_authority(iframe):
    page = FakePage({EMPLOYER: html(FORM + f'<iframe src="{iframe}"></iframe>')})
    route = resolve_application_route(job(), page)
    assert route.status == "verified"
    assert route.application_url == EMPLOYER
    assert route.allowed_origins == ["https://acme.test"]
    assert route.evidence_urls == [EMPLOYER]
    assert page.visited == [EMPLOYER]
    assert not route_allows_url(route, iframe, confirmation=True)


@pytest.mark.parametrize(
    "iframe",
    [
        "https://www.google.com/forms/application",
        "https://www.google.com.evil.test/recaptcha/api2/anchor",
        "https://hcaptcha.com.evil.test/captcha/v1/static/hcaptcha.html",
        "https://newassets.hcaptcha.com/application-form",
        "https://challenges.cloudflare.com/application-form",
        "https://untrusted.test/form",
        "http://www.google.com/recaptcha/api2/anchor",
        "https://user:secret@www.google.com/recaptcha/api2/anchor",
        "https://www.google.com:8443/recaptcha/api2/anchor",
    ],
)
def test_unrecognized_foreign_frames_remain_ambiguous_even_with_a_verification_frame(iframe):
    extra = (
        FORM
        + '<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>'
        + f'<iframe src="{iframe}"></iframe>'
    )
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(extra)}))
    assert route.status == "needs_input"
    assert route.reason_code == "ambiguous_forms"
    assert route.application_url is None


def test_verification_exception_applies_to_iframes_only_not_apply_anchors():
    extra = FORM + '<a href="https://www.google.com/recaptcha/api2/anchor">Apply</a>'
    route = resolve_application_route(job(), FakePage({EMPLOYER: html(extra)}))
    assert route.status == "needs_input"
    assert route.reason_code == "ambiguous_forms"
