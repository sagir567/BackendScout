"""Turn Indeed recommendation emails into scored job candidates."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from backend_scout.collectors import extract_required_skills, extract_years_experience
from backend_scout.gmail import GmailMessageSummary
from backend_scout.models import Job

INDEED_JOB_EMAIL_QUERY = "newer_than:14d from:indeed"
_GENERIC_LINK_TEXT = {
    "apply",
    "apply now",
    "see job",
    "view job",
    "view job details",
    "view jobs",
    "more",
}
_NON_CONTENT_LINES = {
    "apply now",
    "easily apply",
    "new",
    "new!",
    "view job",
    "view job details",
    "save job",
}
_ISRAEL_LOCATION_TERMS = (
    "israel",
    "tel aviv",
    "tel-aviv",
    "herzliya",
    "haifa",
    "jerusalem",
    "petah tikva",
    "ra'anana",
    "raanana",
    "kfar saba",
    "netanya",
    "beer sheva",
    "remote",
    "hybrid",
    "ישראל",
    "תל אביב",
    "הרצליה",
    "חיפה",
    "ירושלים",
    "רעננה",
    "נתניה",
    "פתח תקווה",
    "כפר סבא",
    "באר שבע",
    "מחוז",
    "היברידי",
    "מרחוק",
)

HtmlFetcher = Callable[[str], str | tuple[str, str]]


@dataclass(frozen=True)
class IndeedEmailCollection:
    jobs: list[Job]
    inspected_messages: int
    discovered_links: int
    warnings: list[str] = field(default_factory=list)


def collect_indeed_email_jobs(
    messages: Iterable[GmailMessageSummary],
    fetch_html: HtmlFetcher | None = None,
) -> IndeedEmailCollection:
    """Collect Indeed links, preferring structured job-page evidence when available."""
    fetch = fetch_html or _fetch_html
    jobs: list[Job] = []
    warnings: list[str] = []
    seen_urls: set[str] = set()
    message_count = 0
    discovered_links = 0

    for message in messages:
        message_count += 1
        for link, anchor in _indeed_links(message):
            if link in seen_urls:
                continue
            seen_urls.add(link)
            discovered_links += 1
            fetch_error: str | None = None
            resolved_link = link
            try:
                fetched = fetch(link)
                if isinstance(fetched, tuple):
                    resolved_link, html = fetched
                else:
                    html = fetched
                structured = parse_indeed_job_page(resolved_link, html)
            except (httpx.HTTPError, OSError, TypeError, ValueError) as exc:
                structured = None
                fetch_error = str(exc)
            fallback = _job_from_email_card(message, resolved_link, anchor)
            job = structured or fallback
            if job is None:
                if fetch_error:
                    warnings.append(f"{link}: page details unavailable ({fetch_error})")
                warnings.append(f"{link}: email did not contain enough job details")
                continue
            jobs.append(job)

    return IndeedEmailCollection(
        jobs=jobs,
        inspected_messages=message_count,
        discovered_links=discovered_links,
        warnings=warnings,
    )


def parse_indeed_job_page(url: str, html: str) -> Job | None:
    """Read a schema.org JobPosting from an Indeed job page."""
    soup = BeautifulSoup(html, "html.parser")
    posting = _find_job_posting(soup)
    if posting is None:
        return None
    title = _clean_text(posting.get("title"))
    company = _organization_name(posting.get("hiringOrganization"))
    description_html = posting.get("description")
    description = (
        BeautifulSoup(description_html, "html.parser").get_text(" ", strip=True)
        if isinstance(description_html, str)
        else ""
    )
    if not title or not company or not description:
        return None
    location = _job_location(posting)
    remote_policy = "Remote" if posting.get("jobLocationType") == "TELECOMMUTE" else _work_mode(location)
    salary = _salary_text(posting.get("baseSalary"))
    source_url = _page_job_url(soup, posting) or canonical_indeed_job_url(url) or url
    return Job(
        source="indeed_email",
        source_url=source_url,
        company=company,
        title=title,
        location=location,
        employment_type=_employment_type(posting.get("employmentType")),
        remote_policy=remote_policy,
        salary_text=salary,
        description=description,
        required_skills=extract_required_skills(title, description),
        years_experience=extract_years_experience(description),
        discovered_at=_published_at(posting.get("datePosted")),
    )


def canonical_indeed_job_url(value: str) -> str | None:
    """Reduce Indeed tracking links to a stable Israel job URL keyed by `jk`."""
    candidate = unescape(value.strip())
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").casefold()
    query = parse_qs(parsed.query)
    job_key = next((item for item in query.get("jk", []) if re.fullmatch(r"[A-Za-z0-9]+", item)), None)
    if not job_key or "indeed" not in host:
        return None
    return f"https://il.indeed.com/viewjob?{urlencode({'jk': job_key})}"


def _indeed_links(message: GmailMessageSummary) -> list[tuple[str, Tag | None]]:
    found: list[tuple[str, Tag | None]] = []
    if message.body_html:
        soup = BeautifulSoup(message.body_html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = unescape(str(anchor.get("href"))).strip()
            url = canonical_indeed_job_url(href)
            if url:
                found.append((url, anchor))
            elif _is_job_tracking_link(href, anchor, message.subject):
                found.append((href, anchor))
    searchable = f"{message.body_text}\n{message.snippet}"
    for raw_url in re.findall(r"https?://[^\s<>\"]+", searchable):
        url = canonical_indeed_job_url(raw_url.rstrip(".,);]"))
        if url:
            found.append((url, None))
    return sorted(found, key=lambda item: _link_priority(item[1], message.subject), reverse=True)


def _job_from_email_card(
    message: GmailMessageSummary,
    url: str,
    anchor: Tag | None,
) -> Job | None:
    if anchor is None:
        return None
    subject_title, subject_company = _job_identity_from_subject(message.subject)
    anchor_text = _clean_text(anchor.get_text(" ", strip=True))
    title = subject_title or anchor_text
    if not title or (not subject_company and anchor_text and anchor_text.casefold() in _GENERIC_LINK_TEXT):
        return None
    lines = _card_lines(anchor)
    remaining = [
        line
        for line in lines
        if line.casefold() not in {title.casefold(), (anchor_text or "").casefold()}
        and line.casefold() not in _NON_CONTENT_LINES
    ]
    company = subject_company or next((line for line in remaining if not _looks_like_location(line)), None)
    location = next((line for line in remaining if _looks_like_location(line)), None)
    if not company:
        return None
    description = " ".join(lines) or message.snippet or "Indeed email recommendation."
    return Job(
        source="indeed_email",
        source_url=url,
        company=company,
        title=title,
        location=location,
        remote_policy=_work_mode(location),
        description=description,
        required_skills=extract_required_skills(title, description),
        years_experience=extract_years_experience(description),
        discovered_at=datetime.now(UTC),
    )


def _card_lines(anchor: Tag) -> list[str]:
    selected: Tag = anchor
    for parent in anchor.parents:
        if not isinstance(parent, Tag) or parent.name in {"body", "html"}:
            break
        text = parent.get_text("\n", strip=True)
        lines = _unique_lines(text)
        if 3 <= len(lines) <= 15 and len(text) <= 2_500:
            selected = parent
        if len(lines) > 15 or len(text) > 2_500:
            break
    return _unique_lines(selected.get_text("\n", strip=True))


def _unique_lines(value: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in value.splitlines():
        line = _clean_text(raw_line)
        key = line.casefold()
        if line and key not in seen and len(line) <= 300:
            lines.append(line)
            seen.add(key)
    return lines


def _find_job_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        content = script.string or script.get_text()
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_json(payload):
            item_type = item.get("@type")
            if item_type == "JobPosting" or (
                isinstance(item_type, list) and "JobPosting" in item_type
            ):
                return item
    return None


def _page_job_url(soup: BeautifulSoup, posting: dict[str, Any]) -> str | None:
    candidates: list[Any] = [posting.get("url")]
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    if isinstance(canonical, Tag):
        candidates.append(canonical.get("href"))
    open_graph = soup.find("meta", attrs={"property": "og:url"})
    if isinstance(open_graph, Tag):
        candidates.append(open_graph.get("content"))
    for candidate in candidates:
        if isinstance(candidate, str) and (url := canonical_indeed_job_url(candidate)):
            return url
    return None


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _organization_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _clean_text(value.get("name"))
    return _clean_text(value)


def _job_location(posting: dict[str, Any]) -> str | None:
    raw_locations = posting.get("jobLocation")
    locations = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    rendered: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if not isinstance(address, dict):
            continue
        parts = [
            _clean_text(address.get("addressLocality")),
            _clean_text(address.get("addressRegion")),
            _clean_text(address.get("addressCountry")),
        ]
        text = ", ".join(part for part in parts if part)
        if text and text not in rendered:
            rendered.append(text)
    if rendered:
        return "; ".join(rendered)
    requirements = posting.get("applicantLocationRequirements")
    values = requirements if isinstance(requirements, list) else [requirements]
    names = [
        _clean_text(value.get("name"))
        for value in values
        if isinstance(value, dict) and _clean_text(value.get("name"))
    ]
    return ", ".join(names) or None


def _salary_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    currency = _clean_text(value.get("currency")) or ""
    raw = value.get("value")
    if not isinstance(raw, dict):
        return None
    minimum = raw.get("minValue")
    maximum = raw.get("maxValue")
    unit = _clean_text(raw.get("unitText")) or ""
    if isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)):
        return f"{minimum:g}-{maximum:g} {currency} {unit}".strip()
    if isinstance(raw.get("value"), (int, float)):
        return f"{raw['value']:g} {currency} {unit}".strip()
    return None


def _employment_type(value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    normalized = [_clean_text(item) for item in values]
    return ", ".join(item for item in normalized if item) or None


def _published_at(value: Any) -> datetime:
    text = _clean_text(value)
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _work_mode(location: str | None) -> str | None:
    normalized = (location or "").casefold()
    if "remote" in normalized:
        return "Remote"
    if "hybrid" in normalized:
        return "Hybrid"
    return None


def _looks_like_location(value: str) -> bool:
    normalized = value.casefold()
    return any(term in normalized for term in _ISRAEL_LOCATION_TERMS)


def _is_job_tracking_link(value: str, anchor: Tag, subject: str) -> bool:
    parsed = urlparse(value)
    if (parsed.hostname or "").casefold() != "cts.indeed.com":
        return False
    anchor_text = (_clean_text(anchor.get_text(" ", strip=True)) or "").casefold()
    subject_title, _company = _job_identity_from_subject(subject)
    return anchor_text in {"view job", "learn more", (subject_title or "").casefold()}


def _link_priority(anchor: Tag | None, subject: str) -> int:
    if anchor is None:
        return 0
    anchor_text = (_clean_text(anchor.get_text(" ", strip=True)) or "").casefold()
    subject_title, _company = _job_identity_from_subject(subject)
    if subject_title and anchor_text == subject_title.casefold():
        return 3
    if anchor_text not in _GENERIC_LINK_TEXT:
        return 2
    return 1


def _job_identity_from_subject(subject: str) -> tuple[str | None, str | None]:
    if " @ " not in subject:
        return None, None
    title, company = subject.rsplit(" @ ", 1)
    return _clean_text(title), _clean_text(company)


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _fetch_html(url: str) -> tuple[str, str]:
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
            )
        },
    )
    resolved_url = canonical_indeed_job_url(str(response.url)) or url
    return resolved_url, response.text if response.is_success else ""
