"""Resolve public application routes without entering or transmitting applicant data.

The caller supplies a synchronous, unauthenticated Playwright page (with service
workers disabled). Resolution is inspection, never authorization to submit.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from backend_scout.models import Job


class ApplicationRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "needs_input", "unavailable"]
    reason_code: str
    message: str
    source_url: str
    employer_posting_url: str | None = None
    application_url: str | None = None
    company: str
    title: str
    job_id: str | None = None
    adapter_name: str | None = None
    checked_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    evidence_urls: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    posting_text: str | None = Field(default=None, max_length=30_000)
    _tracker: str = PrivateAttr(default="production")
    _widgets: set[str] = PrivateAttr(default_factory=set)

    @field_validator("checked_at")
    @classmethod
    def utc_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("checked_at must include a timezone")
        return parsed.astimezone(UTC).isoformat()


_ATS = {
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",
    "comeet.com": "comeet",
    "www.comeet.com": "comeet",
    "comeet.co": "comeet",
    "www.comeet.co": "comeet",
}
_COUNTRY_SUFFIXES = {"co.il", "co.uk", "co.in", "co.nz", "co.jp", "co.za", "com.au", "com.br"}
_AUTH = re.compile(
    r"/(?:log-?(?:in|out)|sign-?(?:in|up|out)|register|auth(?:wall)?|oauth\d*|authorize|sso|"
    r"accounts?|sessions?|password|checkpoint|uas|captcha|challenge)(?:[/.;]|$)",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"token|secret|password|credential|session|auth|api.?key|signature|jwt|bearer", re.IGNORECASE
)
_ID_KEYS = {"jobid", "job_id", "positionid", "position_id", "position_uid", "uid", "gh_jid"}
_APPLY_LABEL = re.compile(
    r"\bapply\b|^\s*application\s*$|\b(?:job application|application form|"
    r"submit (?:an? |your )?application)\b",
    re.IGNORECASE,
)
_APPLY_PATH = re.compile(r"(?:^|[/_-])(?:apply|application)(?=[/_-]|$)", re.IGNORECASE)
_CLOSED = re.compile(
    r"\b(?:this (?:job|position|vacancy) (?:is |has been )?(?:closed|filled|no longer available)|"
    r"(?:we are |we're )no longer accepting applications)\b",
    re.IGNORECASE,
)


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).split())


def _origin(url: str) -> str:
    try:
        return "https://" + (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _board(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return any(host == root or host.endswith("." + root) for root in ("linkedin.com", "indeed.com"))


def _verification_frame(url: str) -> bool:
    """Classify an iframe only; this grants no loading or interaction authority."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if host in {"google.com", "www.google.com", "recaptcha.net", "www.recaptcha.net"}:
            return parsed.path.startswith("/recaptcha/")
        if host == "hcaptcha.com" or host.endswith(".hcaptcha.com"):
            return parsed.path.startswith("/captcha/")
        return host == "challenges.cloudflare.com" and parsed.path.startswith(
            "/cdn-cgi/challenge-platform/"
        )
    except ValueError:
        return False


def _non_application_frame(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return _verification_frame(url) or (
            parsed.scheme == "https" and parsed.port in {None, 443}
            and parsed.username is None and parsed.password is None
            and (parsed.hostname, parsed.path) in {
                ("consentcdn.cookiebot.com", "/sdk/bc-v5.min.html"),
                ("i.liadm.com", "/sync-container"),
            }
        )
    except ValueError:
        return False


def _greenhouse_embed_for_job(url: str, company: str, job_id: str) -> bool:
    """Bind a public embed to the posting, without retaining the widget URL."""
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        return (
            parsed.scheme == "https" and parsed.port in {None, 443}
            and parsed.username is None and parsed.password is None
            and parsed.hostname in {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
            and parsed.path == "/embed/job_app"
            and job_id.isdecimal() and query.get("token") == [job_id]
            and query.get("for") == [_normal(company).replace(" ", "")]
        )
    except ValueError:
        return False


def _binding(url: str) -> tuple[str, str, str] | None:
    """Only recognize actual route identifiers, not title-slug guesses."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    provider = _ATS.get(parsed.hostname or "", "employer-form")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)
    if provider in {"ashby", "lever"} and len(parts) >= 2:
        return provider, parts[0].casefold(), parts[1].casefold()
    if provider == "greenhouse" and len(parts) >= 3 and parts[1] == "jobs":
        return provider, parts[0].casefold(), parts[2].casefold()
    if provider == "comeet":
        if parts and parts[-1] in {"apply", "application"}:
            parts.pop()
        if len(parts) >= 3 and parts[0] in {"jobs", "jobs-embed"}:
            tenant = parts[1] if len(parts) == 3 else parts[2]
            return provider, tenant.casefold(), parts[-1].casefold()
        ids = query.get("position_uid") or query.get("position_id")
        companies = query.get("company_uid")
        if ids and len(ids) == 1 and companies and len(companies) == 1:
            return provider, companies[0].casefold(), ids[0].casefold()
    ids = [value for key, values in query.items() if key.casefold() in _ID_KEYS for value in values]
    if len(ids) == 1:
        return provider, parsed.hostname or "", ids[0].casefold()
    if provider == "employer-form":
        match = re.search(r"/(?:jobs?|positions?)/(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
        if match:
            return provider, parsed.hostname or "", match[1]
    return None


def ats_job_identity(url: str | None) -> tuple[str, str, str] | None:
    """Recognize a public ATS posting or apply alias, not an arbitrary redirect."""
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.port not in {None, 443}
                or parsed.username is not None or parsed.password is not None):
            return None
        provider = _ATS.get(parsed.hostname or "")
    except ValueError:
        return None
    slug = r"[A-Za-z0-9_-]+"
    uid = r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
    patterns = {
        "greenhouse": rf"/{slug}/jobs/\d+/?",
        "lever": rf"/{slug}/{uid}(?:/apply)?/?",
        "ashby": rf"/{slug}/{uid}(?:/application)?/?",
    }
    if provider not in patterns or not re.fullmatch(patterns[provider], parsed.path):
        return None
    return _binding(url)


def _safe(url: str, tracker: str, *, widget: bool = False) -> bool:
    try:
        if not isinstance(url, str) or len(url) > 4096 or "\\" in url:
            return False
        if any(ord(c) < 33 or ord(c) == 127 for c in url):
            return False
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
        ):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        with suppress(OSError):
            socket.inet_aton(host)
            return False
        path = unquote(unquote(parsed.path))
        if (
            _AUTH.search(path)
            or re.search(r"%(?:2f|5c|00|0a|0d)|[\\\x00-\x1f]", path, re.IGNORECASE)
            or any(part in {".", ".."} for part in path.split("/"))
        ):
            return False
        query = parse_qs(parsed.query, keep_blank_values=True)
        public_widget = (
            widget
            and _ATS.get(host) == "comeet"
            and _binding(url) is not None
            and parsed.path.startswith(("/jobs/", "/jobs-embed/"))
        )
        for key, values in query.items():
            if unquote(key) != key or (key.casefold() in _ID_KEYS and len(values) != 1):
                return False
            sensitive = _SECRET.search(key) or key.casefold() in {"code", "state", "key", "email"}
            if sensitive and not (
                public_widget
                and key == "token"
                and len(values) == 1
                and re.fullmatch(r"[A-Za-z0-9._~-]{1,256}", values[0])
            ):
                return False
            if any(value.lower().startswith(("https:", "http:", "//")) for value in values):
                return False
        identifiers = [
            value.casefold()
            for key, values in query.items()
            if key.casefold() in _ID_KEYS
            for value in values
        ]
        binding = _binding(url)
        if len(identifiers) > 1 or identifiers and binding and identifiers[0] != binding[2]:
            return False
        if re.search(r"(?:token|password|secret|access_token)=", parsed.fragment, re.IGNORECASE):
            return False
        if host.endswith(".test"):
            return tracker == "test"
        if host.rsplit(".", 1)[-1] in {
            "localhost",
            "local",
            "localdomain",
            "internal",
            "lan",
            "home",
            "invalid",
            "example",
            "onion",
        }:
            return False
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_global for item in addresses
        )
    except (ValueError, TypeError, OSError):
        return False


def safe_public_url(url: str, tracker="production") -> bool:
    """Preflight a public entry before acquiring a session that navigates eagerly.

    There is deliberately no standalone credential/token exception. A public
    Comeet widget token can only be authorized by the resolver's employer chain.
    """
    return _safe(url, str(getattr(tracker, "value", tracker)))


def _official(url: str, company: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host in _ATS:
        return True
    if _board(url) or any(
        marker in host for marker in ("comeet", "greenhouse", "ashbyhq", "lever")
    ):
        return False
    # An unknown third-party form cannot establish employer authority by branding.
    labels = host.split(".")
    index = -3 if len(labels) > 2 and ".".join(labels[-2:]) in _COUNTRY_SUFFIXES else -2
    return labels[index] == _normal(company).replace(" ", "")


# Read visible public text and structural metadata only, never candidate values.
_INSPECT = r"""() => {
  const visible = e => !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const text = e => e.innerText || '';
  return {
    body: document.body ? document.body.innerText : '',
    headings: [...document.querySelectorAll('h1,h2,h3')].filter(visible).map(text),
    content: [...document.querySelectorAll('main,article')].filter(visible)
      .filter(e => !e.parentElement.closest('main,article')).map(text),
    schemas: [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map(e => e.textContent),
    links: [...document.querySelectorAll('a[href]')].filter(visible).map(e => ({
      url: e.getAttribute('href'), label: [text(e), e.getAttribute('aria-label'),
      e.getAttribute('title')].filter(Boolean).join(' ')})),
    frames: [...document.querySelectorAll('iframe[src]')].filter(visible)
      .map(e => e.getAttribute('src')),
    forms: [...document.forms].filter(visible).map(f => ({
      action: f.getAttribute('action') || '',
      application: /\b(apply|application)\b/i.test(text(f) + ' ' + f.id),
      resume: [...f.querySelectorAll('input[type=file]:not(:disabled)')].filter(e =>
        /resume|\bcv\b|\.pdf|\.doc|application\/pdf/i.test([e.name, e.id, e.accept,
          e.getAttribute('aria-label'), ...[...e.labels || []].map(text)].join(' '))).length === 1,
      ids: [...f.querySelectorAll('input[type=hidden]')].filter(e =>
        /^(job_?id|position_?id|position_uid|gh_jid)$/i.test(e.name)).map(e => e.value)
    }))
  };
}"""


def _postings(raw: list[str]) -> list[dict]:
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                yield value
            yield from walk(value.get("@graph", []))

    result = []
    for source in raw:
        try:
            result.extend(walk(json.loads(source)))
        except (ValueError, TypeError):
            continue
    return result


def _identity(view: dict, job: Job, binding: tuple | None) -> str | None:
    postings = _postings(view["schemas"])
    if postings:
        if len(postings) != 1:
            return "ambiguous_identity"
        posting = postings[0]
        organization = posting.get("hiringOrganization", {})
        company = organization.get("name", "") if isinstance(organization, dict) else organization
        if _normal(str(company)) != _normal(job.company):
            return "company_mismatch"
        if _normal(str(posting.get("title", ""))) != _normal(job.title):
            return "title_mismatch"
        identifier = posting.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        if binding and identifier is not None and str(identifier).casefold() != binding[2]:
            return "job_id_mismatch"
        if binding and isinstance(posting.get("url"), str):
            actual = _binding(posting["url"])
            if actual and actual != binding:
                return "job_id_mismatch"
    else:
        lines = {_normal(line) for line in view["headings"] + view["body"].splitlines()}
        if _normal(job.title) not in lines:
            return "title_mismatch"
        if not re.search(
            r"(?<!\w)" + re.escape(_normal(job.company)) + r"(?!\w)", _normal(view["body"])
        ):
            return "company_mismatch"
    if binding and any(
        str(value).casefold() != binding[2] for form in view["forms"] for value in form["ids"]
    ):
        return "job_id_mismatch"
    return None


def _observed_ids(view: dict) -> set[str]:
    values = {str(value).casefold() for form in view["forms"] for value in form["ids"]}
    postings = _postings(view["schemas"])
    if len(postings) == 1:
        identifier = postings[0].get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        if isinstance(identifier, (str, int)):
            values.add(str(identifier).casefold())
    return values


def _posting_text(view: dict, job: Job) -> str | None:
    postings = _postings(view["schemas"])
    if len(postings) == 1 and isinstance(postings[0].get("description"), str):
        text = BeautifulSoup(postings[0]["description"], "html.parser").get_text("\n", strip=True)
        if text:
            return text[:30_000]
    content = [
        text
        for text in view.get("content", [])
        if _normal(job.title) in {_normal(line) for line in text.splitlines()}
    ]
    if len(content) == 1:
        return content[0].strip()[:30_000] or None
    return None


def route_allows_url(route: ApplicationRoute, url: str, *, confirmation: bool = False) -> bool:
    """Check route scope, not permission to send. Confirmation adds no new origin."""
    if route.status != "verified" or not route.application_url:
        return False
    # Persisted routes retain the observed employer/widget chain, not private state.
    widgets = set(route._widgets)
    employer = route.employer_posting_url
    if (
        employer
        and employer in route.evidence_urls
        and safe_public_url(employer, tracker=route._tracker)
        and _official(employer, route.company)
        and _origin(employer) in route.allowed_origins
    ):
        for item in route.evidence_urls:
            binding = _binding(item)
            if (
                binding
                and binding[0] == "comeet"
                and binding[2] == route.job_id
                and _origin(item) in route.allowed_origins
            ):
                widgets.add(item)
    if not _safe(route.application_url, route._tracker, widget=route.application_url in widgets):
        return False
    if not _safe(url, route._tracker, widget=url in widgets):
        return False
    if _board(url) or _origin(url) not in route.allowed_origins:
        return False
    base = route.application_url
    expected, actual = _binding(base), _binding(url)
    if actual and (expected is None or actual != expected):
        return False
    observed = [base, route.employer_posting_url, *route.evidence_urls]
    if any(
        item
        and _origin(item) == _origin(url)
        and urlsplit(item).path == urlsplit(url).path
        and parse_qs(urlsplit(item).query) == parse_qs(urlsplit(url).query)
        for item in observed
    ):
        return True
    if not confirmation or _origin(url) != _origin(base):
        return False
    if actual:
        return actual == expected
    # Custom AJAX/receipt paths may omit a job ID; another vacancy may not.
    return not re.search(
        r"/(?:jobs?|careers?|positions?)(?:/|$)", urlsplit(url).path, re.IGNORECASE
    )


def resolve_application_route(job: Job, page, *, tracker="test", verified_employer_hosts=()) -> ApplicationRoute:
    """Inspect at most four pages (three observed-link hops); never click or fill."""
    tracker = str(getattr(tracker, "value", tracker))
    evidence: list[str] = []
    widgets: set[str] = set()
    employer = None
    expected = None
    posting_text = None
    trusted_hosts = frozenset(verified_employer_hosts)

    def official(url):
        return (_official(url, job.company)
                or (urlsplit(url).hostname or "") in trusted_hosts)

    def result(status, reason, message, application=None):
        route = ApplicationRoute(
            status=status,
            reason_code=reason,
            message=message,
            source_url=job.source_url,
            employer_posting_url=employer,
            application_url=application,
            company=job.company,
            title=job.title,
            job_id=expected[2] if expected else None,
            adapter_name=(
                _ATS.get(urlsplit(application).hostname or "", "employer-form")
                if application
                else None
            ),
            evidence_urls=list(evidence),
            allowed_origins=list(
                dict.fromkeys(_origin(item) for item in (employer, application) if item)
            )
            if application
            else [],
            posting_text=posting_text,
        )
        route._tracker, route._widgets = tracker, widgets.copy()
        return route

    explicit = job.application_url
    current = (
        explicit
        if explicit and _safe(explicit, tracker) and not _board(explicit)
        else job.source_url
    )
    if not _safe(current, tracker):
        return result("needs_input", "unsafe_url", "A safe public HTTPS posting URL is required.")
    expected = _binding(current) if not _board(current) else None
    source_binding = _binding(job.source_url) if not _board(job.source_url) else None
    if (
        expected
        and source_binding
        and expected[0] == source_binding[0]
        and expected != source_binding
    ):
        return result(
            "needs_input", "job_id_mismatch", "The supplied links identify different jobs."
        )

    blocked = False
    navigation_count = 0

    def guard(intercept):
        nonlocal blocked, navigation_count
        request = intercept.request
        navigation = request.is_navigation_request()
        main = navigation and request.frame == page.main_frame
        allowed = request.method in {"GET", "HEAD"} and _safe(
            request.url, tracker, widget=request.url in widgets
        )
        if navigation:
            allowed = allowed and _origin(request.url) == _origin(current)
            if main:
                navigation_count += 1
                actual = _binding(request.url)
                allowed = (
                    allowed
                    and navigation_count <= 4
                    and not (expected and actual and expected != actual)
                )
        if allowed:
            fallback = getattr(intercept, "fallback", None)
            if callable(fallback):
                fallback()
            else:
                intercept.continue_()
        else:
            blocked = blocked or main
            intercept.abort()

    installed = False
    try:
        page.route("**/*", guard)
        installed = True
        for hop in range(4):
            response = page.goto(current, wait_until="domcontentloaded", timeout=20_000)
            if blocked or not _safe(page.url, tracker, widget=page.url in widgets):
                return result(
                    "needs_input", "unsafe_redirect", "Navigation left the safe public route."
                )
            if _origin(page.url) != _origin(current):
                return result(
                    "needs_input", "unsafe_redirect", "An unobserved origin change was rejected."
                )
            current = page.url
            host = urlsplit(current).hostname or ""
            if (
                _ATS.get(host) == "comeet"
                and (host.endswith(".co") or "/jobs-embed/" in current)
                and current not in widgets
            ):
                return result(
                    "needs_input",
                    "unbound_widget",
                    "An embedded widget requires a verified employer link.",
                )
            if response and response.status in {404, 410}:
                return result(
                    "unavailable", "posting_unavailable", "The public page was not found."
                )
            if response and response.status >= 400:
                return result(
                    "needs_input",
                    "public_page_unavailable",
                    "The public page could not be inspected.",
                )
            view = page.evaluate(_INSPECT)
            binding = _binding(current) if not _board(current) else None
            if expected and binding and expected != binding:
                return result(
                    "needs_input", "job_id_mismatch", "The page identifies a different job."
                )
            mismatch = _identity(view, job, binding or expected)
            if mismatch:
                return result(
                    "needs_input",
                    mismatch,
                    "The public page does not establish the exact company and role.",
                )
            observed_ids = _observed_ids(view)
            if len(observed_ids) > 1:
                return result(
                    "needs_input",
                    "job_id_mismatch",
                    "The page contains conflicting job identifiers.",
                )
            expected = binding or expected
            evidence.append(current)
            if _CLOSED.search(view["body"]):
                return result(
                    "unavailable",
                    "vacancy_closed",
                    "The role page explicitly says applications are closed.",
                )
            is_official = official(current)
            if is_official and not _ATS.get(urlsplit(current).hostname or "") and employer is None:
                employer = current
            if not is_official and not _board(current):
                return result(
                    "needs_input",
                    "untrusted_employer",
                    "Employer authority could not be established.",
                )
            if is_official:
                text = _posting_text(view, job)
                if text and len(text) > len(posting_text or ""):
                    posting_text = text
            candidates = set()
            unsafe_link = False
            urls = [
                link["url"]
                for link in view["links"]
                if _APPLY_LABEL.search(link["label"]) or _APPLY_PATH.search(urlsplit(link["url"]).path)
            ]
            frames = [urljoin(current, src) for src in view["frames"]]
            frames = [src for src in frames if not _non_application_frame(src)]
            if binding and binding[0] == "comeet":
                frames = [
                    src for src in frames
                    if not (
                        _ATS.get(urlsplit(src).hostname or "") == "comeet"
                        and urlsplit(src).path.casefold()
                        == f"/jobs/{binding[1]}/{binding[2]}/social"
                    )
                ]
            embedded = bool(
                employer == current and expected and len(frames) == 1
                and _greenhouse_embed_for_job(frames[0], job.company, expected[2])
            )
            urls.extend(frames if not embedded else [])
            for href in urls:
                target = urljoin(current, href)
                widget = bool(
                    employer
                    and _origin(current) == _origin(employer)
                    and _ATS.get(urlsplit(target).hostname or "") == "comeet"
                )
                if not _safe(target, tracker, widget=widget):
                    unsafe_link = True
                    continue
                if _board(target) or not official(target):
                    unsafe_link = True
                    continue
                if target.split("#", 1)[0] == current.split("#", 1)[0]:
                    continue
                target_binding = _binding(target)
                if (
                    expected
                    and target_binding
                    and (
                        expected[0] == target_binding[0]
                        and expected != target_binding
                        or widget
                        and expected[2] != target_binding[2]
                    )
                ) or (
                    widget
                    and target_binding
                    and observed_ids
                    and target_binding[2] not in observed_ids
                ):
                    return result(
                        "needs_input",
                        "job_id_mismatch",
                        "An apply link identifies a different job.",
                    )
                if widget and not target_binding:
                    unsafe_link = True
                    continue
                if widget:
                    widgets.add(target)
                candidates.add(target)
            if len(candidates) > 1 or candidates and unsafe_link:
                return result(
                    "needs_input",
                    "ambiguous_apply_links",
                    "There is no unique trusted apply destination.",
                )
            forms = [form for form in view["forms"] if form["resume"] or form["application"]]
            if embedded and not candidates and not unsafe_link and not forms:
                return result(
                    "verified", "greenhouse_embed_manual",
                    "The employer page embeds a Greenhouse form for the exact role; apply on the employer page.",
                    current,
                )
            if forms and not _board(current):
                if len(forms) != 1 or candidates or unsafe_link:
                    return result(
                        "needs_input",
                        "ambiguous_forms",
                        "There is no unique trusted application form.",
                    )
                form = forms[0]
                action = urljoin(current, form["action"])
                if not form["resume"] or not form["application"]:
                    if (
                        form["resume"]
                        and not _ATS.get(host)
                        and _safe(action, tracker)
                        and _origin(action) == _origin(current)
                        and urlsplit(action).path == urlsplit(current).path
                        and {
                            key: values for key, values in parse_qs(urlsplit(action).query).items()
                            if key not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
                        } == parse_qs(urlsplit(current).query)
                    ):
                        return result(
                            "verified", "resume_form_manual",
                            "The official posting has a resume form without explicit application context.",
                            current,
                        )
                    return result(
                        "needs_input",
                        "application_context_missing",
                        "A unique resume application form was not established.",
                    )
                if (
                    not _safe(action, tracker, widget=action in widgets)
                    or _origin(action) != _origin(current)
                    or (_binding(action) and _binding(action) != expected)
                ):
                    return result(
                        "needs_input",
                        "untrusted_form_action",
                        "The form action is outside the verified job route.",
                    )
                return result(
                    "verified",
                    "official_application_verified",
                    "A public application form is bound to the exact role.",
                    current,
                )
            if not candidates:
                if not unsafe_link and binding and binding[0] == "comeet" and host in {
                    "comeet.com", "www.comeet.com",
                } and hasattr(page, "locator"):
                    reveal = page.locator('button#showApplyForm[type="button"], button#showApplyForm:not([type])')
                    if reveal.count() == 1 and reveal.is_visible() and not reveal.evaluate("button => Boolean(button.form)"):
                        reveal.click(timeout=2500)
                        frame = page.locator('#jobFormWrapper iframe[src*="/apply"]')
                        frame.first.wait_for(state="attached", timeout=3000)
                        if frame.count() == 1 and page.url == current:
                            target = urljoin(current, frame.get_attribute("src") or "")
                            if _binding(target) == binding and _safe(target, tracker, widget=True):
                                return result(
                                    "verified", "comeet_reveal_manual",
                                    "The verified posting opens an embedded Comeet form for the exact role.",
                                    current,
                                )
                reason = "unsafe_apply_link" if unsafe_link else "application_route_missing"
                return result(
                    "needs_input",
                    reason,
                    "No unique public application destination was established.",
                )
            if hop == 3:
                return result(
                    "needs_input", "hop_limit", "The public apply-link hop limit was reached."
                )
            current = candidates.pop()
            # Cross-provider IDs are independent; the next exact identity check binds them.
            expected = _binding(current)
        raise AssertionError("unreachable")
    except Exception:  # noqa: BLE001 - browser errors can contain credential-bearing URLs
        # Browser exceptions can include query credentials; never echo their text.
        reason = "unsafe_redirect" if blocked else "inspection_failed"
        return result("needs_input", reason, "The public route could not be safely inspected.")
    finally:
        if installed:
            with suppress(Exception):
                page.unroute("**/*", guard)
