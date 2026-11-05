"""Read-only, exact-posting enrichment; no search, credentials or application API.

``fetcher(url) -> httpx.Response`` may replace the HTTP boundary (for example a
MockTransport-backed client's GET). It must perform one GET without following
redirects. Responses, including injected streaming responses, are closed here.
Only the default transport is responsible for enforcing socket timeouts; all
responses still pass the URL, redirect, request-count and decoded-body limits.

Supported contracts: Greenhouse Job Board GET, Ashby public posting-api and
Lever public postings GET (global/EU). Public board HTML is limited to JobPosting
JSON-LD, plus LinkedIn's identifiable public guest description. No browser,
login, guest API workaround, arbitrary employer scraping or inferred job text.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from backend_scout.application_urls import application_entry_url
from backend_scout.collectors import extract_required_skills, extract_years_experience
from backend_scout.models import Job

Fetcher = Callable[[str], httpx.Response]
MAX_REQUESTS = 4
MAX_REDIRECTS = 2
MAX_BODY_BYTES = 2_000_000
MAX_DESCRIPTION_CHARS = 30_000
TIMEOUT_SECONDS = 8.0
TOTAL_SECONDS = 25.0

_ATS_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "boards-api.greenhouse.io",
    "jobs.ashbyhq.com",
    "api.ashbyhq.com",
    "jobs.lever.co",
    "api.lever.co",
    "jobs.eu.lever.co",
    "api.eu.lever.co",
}
_BOARD_HOSTS = {
    "linkedin.com",
    "www.linkedin.com",
    "il.linkedin.com",
    "indeed.com",
    "www.indeed.com",
    "il.indeed.com",
    "lin-srael.com",
    "www.lin-srael.com",
    "comeet.com",
    "www.comeet.com",
}
_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}"
_AUTH_PATH = re.compile(
    r"/(?:login|signin|sign-in|signup|sign-up|authwall|checkpoint|uas|oauth|auth)(?:/|$)",
    re.IGNORECASE,
)
_SECRET_QUERY = re.compile(
    r"(?:password|passwd|secret|token|authorization|credential|api[_-]?key|signature)",
    re.IGNORECASE,
)
_GATED = re.compile(
    r"verify (?:that )?you are human|unusual traffic|checking your browser|"
    r"enable javascript and cookies|sign in to (?:view|see|continue)|"
    r"log in to (?:view|see|continue)|accept cookies to (?:view|continue)",
    re.IGNORECASE,
)
_CLOSED = re.compile(
    r"no longer accepting applications|(?:this |the )?job (?:is |has )?"
    r"(?:no longer available|expired|been closed)|position has been filled",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EnrichmentResult:
    job: Job | None
    status: Literal["enriched", "unavailable"]
    reason_code: str
    message: str
    evidence_urls: tuple[str, ...] = ()

    @property
    def evidence_url(self) -> str | None:
        return self.evidence_urls[-1] if self.evidence_urls else None


@dataclass(frozen=True)
class _Posting:
    provider: str
    board: str
    job_id: str
    region: str = ""


class _Unavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


def _check_url(url: str) -> None:
    try:
        if not isinstance(url, str) or len(url) > 4096:
            raise ValueError
        if any(ord(char) < 33 or ord(char) == 127 for char in url) or "\\" in url:
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or re.search(r"%(?:2f|5c|00|0a|0d)", parsed.path, re.IGNORECASE)
        ):
            raise ValueError
        query = parse_qs(parsed.query, keep_blank_values=True)
        public_embed = (
            parsed.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}
            and parsed.path == "/embed/job_app"
            and set(query) == {"for", "token"}
            and len(query["for"]) == len(query["token"]) == 1
            and re.fullmatch(_TOKEN, query["for"][0])
            and re.fullmatch(r"\d{1,20}", query["token"][0])
        )
        if any(
            (_SECRET_QUERY.search(key) and not (public_embed and key == "token"))
            or key.casefold() in {"code", "state"}
            for key in query
        ):
            raise ValueError
        if any(
            value.casefold().startswith(("http:", "https:", "//"))
            for values in query.values()
            for value in values
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise _Unavailable(
            "unsafe_url", "The posting URL is not a safe public HTTPS URL."
        ) from None
    if parsed.hostname not in _ATS_HOSTS | _BOARD_HOSTS:
        raise _Unavailable("unsupported_url", "This host has no approved public posting parser.")
    if _AUTH_PATH.search(unquote(parsed.path)):
        raise _Unavailable("source_gated", "The source requires authentication or verification.")


def _posting(url: str) -> _Posting | None:
    _check_url(url)
    parsed = urlsplit(url)
    host, path = parsed.hostname, parsed.path.rstrip("/")
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"}:
        match = re.fullmatch(rf"/({_TOKEN})/jobs/(\d+)", path)
        if not match and path == "/embed/job_app":
            query = parse_qs(parsed.query)
            board, job_id = query.get("for", []), query.get("token", [])
            if len(board) == len(job_id) == 1:
                match = re.fullmatch(rf"/({_TOKEN})/jobs/(\d+)", f"/{board[0]}/jobs/{job_id[0]}")
        if match:
            return _Posting("greenhouse", match[1], match[2])
    if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}:
        match = re.fullmatch(rf"/({_TOKEN})/({_UUID})(?:/(?:apply|application))?", path)
        if match:
            provider = "ashby" if host == "jobs.ashbyhq.com" else "lever"
            return _Posting(
                provider, match[1], match[2].lower(), "eu" if host == "jobs.eu.lever.co" else ""
            )
    if host in {"linkedin.com", "www.linkedin.com", "il.linkedin.com"}:
        match = re.fullmatch(r"/jobs/view/(?:[^/]+-)?(\d+)", path)
        if match:
            return _Posting("linkedin", "", match[1])
    if host in {"indeed.com", "www.indeed.com", "il.indeed.com"} and path == "/viewjob":
        keys = parse_qs(parsed.query).get("jk", [])
        if len(keys) == 1 and re.fullmatch(r"[A-Za-z0-9]+", keys[0]):
            return _Posting("indeed", "", keys[0])
    if host in {"comeet.com", "www.comeet.com"} and re.fullmatch(
        r"/jobs/[^/]+/[^/]+(?:/[^/]+)*", path
    ):
        return _Posting("comeet", "", path)
    if host in {"lin-srael.com", "www.lin-srael.com"} and re.fullmatch(r"/jobs?/[^/]+", path):
        return _Posting("lin_srael", "", path)
    return None


def _api_url(posting: _Posting) -> str | None:
    if posting.provider == "greenhouse":
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{posting.board}/jobs/"
            f"{posting.job_id}?content=true"
        )
    if posting.provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{posting.board}"
    if posting.provider == "lever":
        host = "api.eu.lever.co" if posting.region == "eu" else "api.lever.co"
        return f"https://{host}/v0/postings/{posting.board}/{posting.job_id}?mode=json"
    return None


class _Reader:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self.evidence: list[str] = []
        self.requests = 0
        self.deadline = time.monotonic() + TOTAL_SECONDS

    def _check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise _Unavailable("fetch_timeout", "The bounded public posting fetch timed out.")

    def read(self, url: str, posting: _Posting) -> tuple[bytes, str, str]:
        original = url
        for redirects in range(MAX_REDIRECTS + 1):
            _check_url(url)
            self._check_deadline()
            if self.requests >= MAX_REQUESTS:
                raise _Unavailable("request_limit", "The public posting request limit was reached.")
            self.requests += 1
            self.evidence.append(url)
            response = self.fetcher(url)
            if not isinstance(response, httpx.Response):
                raise _Unavailable(
                    "invalid_response", "The fetcher did not return an HTTP response."
                )
            try:
                if response.history:
                    raise _Unavailable("redirect_blocked", "Automatic redirects are not permitted.")
                try:
                    request = response.request
                except RuntimeError:
                    request = None
                if request and (request.method != "GET" or request.url != httpx.URL(url)):
                    raise _Unavailable(
                        "redirect_blocked", "The response is not from the requested public URL."
                    )
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    destination = urljoin(url, response.headers.get("location", ""))
                    _check_url(destination)
                    api = _api_url(posting)
                    if api:
                        permitted = httpx.URL(destination) == httpx.URL(original)
                    else:
                        permitted = _posting(destination) == posting
                    if not permitted or redirects == MAX_REDIRECTS or destination == url:
                        raise _Unavailable(
                            "redirect_blocked",
                            "The redirect does not preserve the trusted posting.",
                        )
                    url = destination
                    continue
                if status in {404, 410}:
                    raise _Unavailable(
                        "closed", "The posting is missing, closed or no longer public."
                    )
                if status in {401, 403, 429, 999}:
                    raise _Unavailable(
                        "source_gated", "The source denied public access or requires verification."
                    )
                if status != 200:
                    raise _Unavailable(
                        "http_error", "The public source did not return a successful posting."
                    )
                length = response.headers.get("content-length", "")
                if length.isdecimal() and int(length) > MAX_BODY_BYTES:
                    raise _Unavailable(
                        "response_too_large", "The posting response exceeds the size limit."
                    )
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=16_384):
                    self._check_deadline()
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        raise _Unavailable(
                            "response_too_large", "The posting response exceeds the size limit."
                        )
                    body.extend(chunk)
                return bytes(body), response.headers.get("content-type", "").lower(), url
            finally:
                response.close()
        raise AssertionError("redirect loop exceeded its bound")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _plain(value: Any) -> str:
    # Greenhouse can HTML-escape the HTML content as well as its inner text.
    html = _text(value)
    for _ in range(2):
        decoded = unescape(html)
        if decoded == html:
            break
        html = decoded
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def _identity(job: Job, title: Any, company: Any = None) -> None:
    if not _text(title):
        raise _Unavailable("invalid_response", "The posting has no verifiable title.")
    if _normalized(_plain(title)) != _normalized(job.title):
        raise _Unavailable("identity_mismatch", "The public posting title does not match this job.")
    if isinstance(company, dict):
        company = company.get("name")
    if company is not None and (
        not _text(company) or _normalized(_plain(company)) != _normalized(job.company)
    ):
        raise _Unavailable(
            "identity_mismatch", "The public posting company does not match this job."
        )


def _description(value: str, job: Job) -> str:
    if _GATED.search(value):
        raise _Unavailable(
            "source_gated", "The source returned a sign-in or verification placeholder."
        )
    if _CLOSED.search(value):
        raise _Unavailable("closed", "The public posting says the role is no longer available.")
    if len(value) > MAX_DESCRIPTION_CHARS:
        raise _Unavailable(
            "description_too_large", "The full description exceeds the supported limit."
        )
    words = _normalized(value).split()
    if (
        len(value) < 160
        or len(words) < 25
        or len(set(words)) < 15
        or _normalized(value) in {_normalized(job.title), _normalized(job.description)}
        and len(value) < 200
    ):
        raise _Unavailable(
            "description_incomplete", "The source did not expose a substantial full description."
        )
    return value


def _same_url(value: Any, posting: _Posting) -> bool:
    if not _text(value):
        return False
    try:
        return _posting(value) == posting
    except _Unavailable:
        return False


def _ats_body(job: Job, posting: _Posting, body: bytes) -> tuple[str, str | None]:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise _Unavailable("invalid_response", "The public ATS returned invalid JSON.") from None
    if posting.provider == "ashby":
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise _Unavailable("invalid_response", "The Ashby response has no public jobs list.")
        matches = [
            item
            for item in payload["jobs"]
            if isinstance(item, dict) and _same_url(item.get("jobUrl"), posting)
        ]
        if not matches:
            raise _Unavailable("closed", "The exact job is no longer on the public Ashby board.")
        if len(matches) != 1:
            raise _Unavailable(
                "identity_mismatch", "The board returned ambiguous posting identities."
            )
        item = matches[0]
        if "id" in item and str(item["id"]).lower() != posting.job_id:
            raise _Unavailable("identity_mismatch", "The Ashby posting ID conflicts with its URL.")
        if item.get("isListed") is False:
            raise _Unavailable(
                "unlisted", "The posting is unlisted; its public availability is unverified."
            )
    else:
        item = payload
        if not isinstance(item, dict):
            raise _Unavailable("invalid_response", "The ATS returned no exact posting object.")
        if str(item.get("id", "")).lower() != posting.job_id:
            raise _Unavailable("identity_mismatch", "The ATS returned a different posting ID.")
    company = item.get("company_name", item.get("companyName", item.get("company")))
    _identity(job, item.get("text") if posting.provider == "lever" else item.get("title"), company)
    if posting.provider == "greenhouse":
        description = _plain(item.get("content"))
        urls = [item.get("absolute_url")]
    elif posting.provider == "ashby":
        description = _text(item.get("descriptionPlain")) or _plain(item.get("descriptionHtml"))
        urls = [item.get("applyUrl"), item.get("jobUrl")]
    else:
        parts = [_text(item.get("descriptionPlain")) or _plain(item.get("description"))]
        lists = item.get("lists", [])
        if not isinstance(lists, list) or any(not isinstance(section, dict) for section in lists):
            raise _Unavailable("invalid_response", "The Lever requirements list is malformed.")
        for section in lists:
            parts.extend([_plain(section.get("text")), _plain(section.get("content"))])
        parts.extend(
            [
                _text(item.get("additionalPlain")) or _plain(item.get("additional")),
                _text(item.get("salaryDescriptionPlain")) or _plain(item.get("salaryDescription")),
            ]
        )
        description = " ".join(part for part in parts if part)
        urls = [item.get("applyUrl"), item.get("hostedUrl")]
    for url in urls:
        if not _text(url):
            continue
        # Employer-hosted Greenhouse absolute_url is not assumed to be an ATS URL.
        # It is neither fetched nor promoted; the matched input ATS link is usable.
        if not _same_url(url, posting):
            if posting.provider == "greenhouse":
                try:
                    _check_url(url)
                except _Unavailable as exc:
                    if exc.code == "unsupported_url":
                        continue
            raise _Unavailable(
                "identity_mismatch", "The ATS posting links do not match the requested job."
            )
    return _description(description, job), next(
        (url for url in urls if _same_url(url, posting)), None
    )


def _schema_postings(payload: Any) -> Iterator[dict[str, Any]]:
    # Inspect only JSON-LD roots and @graph, never nested recommendation widgets.
    stack = [(payload, 0)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > 500 or depth > 8:
            raise _Unavailable("invalid_response", "The structured posting exceeds parsing limits.")
        if isinstance(value, list):
            stack.extend((child, depth + 1) for child in reversed(value))
        elif isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                yield value
            if "@graph" in value:
                stack.append((value["@graph"], depth + 1))


def _expired(value: Any) -> bool:
    if not _text(value):
        return False
    try:
        if len(value) == 10:
            return date.fromisoformat(value) < datetime.now(UTC).date()
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or UTC) < datetime.now(UTC)
    except ValueError:
        raise _Unavailable("invalid_response", "The posting expiry date is malformed.") from None


def _html_body(job: Job, posting: _Posting, body: bytes, url: str) -> tuple[str, str | None]:
    soup = BeautifulSoup(body, "html.parser")
    heading = " ".join(tag.get_text(" ", strip=True) for tag in soup.select("title, h1"))
    visible = soup.get_text(" ", strip=True)
    if re.search(
        r"sign in|log in|authwall|just a moment|access denied|security verification|before you continue",
        heading,
        re.IGNORECASE,
    ) or _GATED.search(visible):
        raise _Unavailable(
            "source_gated", "The public source returned a login, cookie or anti-bot page."
        )
    if _CLOSED.search(visible):
        raise _Unavailable("closed", "The public page says the role is no longer available.")
    canonical = soup.find("link", rel="canonical")
    canonical_url = urljoin(url, str(canonical.get("href", ""))) if canonical else None
    if canonical_url and not _same_url(canonical_url, posting):
        raise _Unavailable(
            "identity_mismatch", "The page canonical URL belongs to a different posting."
        )
    candidates = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=20):
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError, RecursionError):
            continue
        candidates.extend(_schema_postings(payload))
    matches = []
    for candidate in candidates:
        if "url" in candidate and not _same_url(candidate["url"], posting):
            continue
        identifier = candidate.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        if (
            posting.provider in {"linkedin", "indeed"}
            and identifier is not None
            and str(identifier) != posting.job_id
        ):
            continue
        try:
            _identity(job, candidate.get("title"), candidate.get("hiringOrganization"))
        except _Unavailable:
            continue
        matches.append(candidate)
    if len(matches) > 1:
        raise _Unavailable(
            "identity_mismatch", "The page contains ambiguous matching JobPosting objects."
        )
    if matches:
        candidate = matches[0]
        if _expired(candidate.get("validThrough")):
            raise _Unavailable("closed", "The public posting has expired.")
        description = _description(_plain(candidate.get("description")), job)
        return description, url if posting.provider == "comeet" else None
    if candidates:
        raise _Unavailable("identity_mismatch", "The structured posting does not match this job.")
    if posting.provider == "linkedin" and canonical_url:
        titles = soup.select(".top-card-layout__title")
        companies = soup.select(".topcard__org-name-link")
        descriptions = soup.select(".description__text .show-more-less-html__markup")
        if len(titles) == len(companies) == len(descriptions) == 1:
            _identity(
                job, titles[0].get_text(" ", strip=True), companies[0].get_text(" ", strip=True)
            )
            return _description(_plain(str(descriptions[0])), job), None
    raise _Unavailable("no_description", "No identifiable full public job description was exposed.")


def enrich_job(job: Job, *, fetcher: Fetcher | None = None) -> EnrichmentResult:
    """Return a deep job copy or ``unavailable`` with ``job=None`` for manual fallback.

    A supplied recognized ATS application URL is tried before the source URL.
    No candidate facts or original job fields are mutated. Only description,
    body-derived skills/years and a verified exact ATS/Comeet application URL
    are changed. Unknown employer/discovery URLs are not promoted as apply links.
    Failure reason codes and evidence URLs are suitable for service/UI handling;
    callers must not queue a CV when status is ``unavailable``.
    """
    candidates: list[tuple[str, _Posting]] = []
    failures: list[_Unavailable] = []
    for url in dict.fromkeys(filter(None, [job.application_url, job.source_url])):
        try:
            posting = _posting(url)
            if posting is None:
                raise _Unavailable(
                    "unsupported_url", "This URL is not a supported exact public posting."
                )
            if url == job.application_url and posting.provider not in {
                "greenhouse",
                "lever",
                "ashby",
                "comeet",
            }:
                continue
            candidates.append((url, posting))
        except _Unavailable as exc:
            failures.append(exc)
    with ExitStack() as stack:
        if fetcher is None and candidates:
            client = stack.enter_context(
                httpx.Client(
                    timeout=TIMEOUT_SECONDS,
                    follow_redirects=False,
                    trust_env=False,
                    headers={
                        "User-Agent": "BackendScout/0.1",
                        "Accept": "application/json, text/html",
                    },
                )
            )

            def fetcher(url: str) -> httpx.Response:
                return client.send(
                    client.build_request("GET", url), stream=True, follow_redirects=False
                )

        reader = _Reader(fetcher)  # type: ignore[arg-type]
        for url, posting in candidates:
            try:
                api_url = _api_url(posting)
                body, content_type, resolved_url = reader.read(api_url or url, posting)
                if api_url:
                    if "json" not in content_type:
                        raise _Unavailable(
                            "source_gated", "The ATS did not expose a public JSON posting."
                        )
                    description, application_url = _ats_body(job, posting, body)
                    application_url = application_url or url
                else:
                    if (
                        "text/html" not in content_type
                        and "application/xhtml+xml" not in content_type
                    ):
                        raise _Unavailable(
                            "invalid_response", "The public page did not return HTML."
                        )
                    description, application_url = _html_body(job, posting, body, resolved_url)
                enriched = job.model_copy(
                    deep=True,
                    update={
                        "description": description,
                        "required_skills": extract_required_skills("", description),
                        "years_experience": extract_years_experience(description),
                        "application_url": application_url or _existing_application_url(job),
                    },
                )
                return EnrichmentResult(
                    enriched,
                    "enriched",
                    "full_description_found",
                    "Fetched a matching full public job description.",
                    tuple(reader.evidence),
                )
            except _Unavailable as exc:
                failures.append(exc)
            except httpx.TimeoutException:
                failures.append(
                    _Unavailable("fetch_timeout", "The public posting fetch timed out.")
                )
            except (httpx.HTTPError, OSError):
                failures.append(
                    _Unavailable("fetch_failed", "The public posting could not be fetched.")
                )
        failure = (
            failures[-1]
            if failures
            else _Unavailable("unsupported_url", "No supported public posting URL is available.")
        )
        return EnrichmentResult(
            None,
            "unavailable",
            failure.code,
            f"{failure.message} Paste the full posting manually or use another verified source.",
            tuple(reader.evidence),
        )


def _existing_application_url(job: Job) -> str | None:
    """Retain a verified route already supplied by the tracker; never guess one."""
    value = application_entry_url(job.application_url)
    if not value or value == job.source_url:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.port not in {None, 443}
        or parsed.hostname in _BOARD_HOSTS | _ATS_HOSTS
    ):
        return None
    if any(_SECRET_QUERY.search(key) for key in parse_qs(parsed.query)):
        return None
    return value
