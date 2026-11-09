"""Offline, evidence-only parser for initial synthetic SecretHunter email formats.

This does not claim compatibility with a live recommendation template.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag

from backend_scout.collectors import extract_required_skills, extract_years_experience
from backend_scout.gmail import GmailMessageSummary
from backend_scout.models import Job

_MAX_BODY = 500_000
_MAX_LINKS = 500
_UNSUPPORTED = "SecretHunter email: unsupported or ambiguous job evidence skipped."


@dataclass(frozen=True)
class SecretHunterEmailCollection:
    jobs: list[Job]
    inspected_messages: int
    discovered_links: int
    warnings: list[str] = field(default_factory=list)


def canonical_secrethunter_job_url(value: str) -> str | None:
    """Accept only the observed selected-ID route, never redirects or auth URLs."""
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "secrethunter.io"
            or parsed.path != "/search"
            or parsed.fragment
        ):
            return None
        selected = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=50).get(
            "selected", []
        )
    except ValueError:
        return None
    if len(selected) != 1 or not re.fullmatch(r"[A-Za-z0-9]+", selected[0]):
        return None
    return f"https://secrethunter.io/search?selected={selected[0]}"


def collect_secrethunter_email_jobs(
    messages: Iterable[GmailMessageSummary],
) -> SecretHunterEmailCollection:
    """Inspect supplied messages only; count distinct accepted links across messages."""
    jobs: dict[str, Job] = {}
    ambiguous: set[str] = set()
    discovered: set[str] = set()
    warnings: list[str] = []
    inspected = 0
    for message in messages:
        inspected += 1
        address = parseaddr(message.from_header)[1]
        if address.count("@") != 1 or address.rsplit("@", 1)[1].lower() not in {
            "secrethunter.io",
            "mail.secrethunter.io",
        }:
            continue
        if len(message.body_html) + len(message.body_text) > _MAX_BODY:
            warnings.append("SecretHunter email: body limit exceeded; message skipped.")
            continue
        soup = BeautifulSoup(message.body_html, "html.parser")
        anchors = soup.find_all("a", href=True, limit=_MAX_LINKS + 1)
        if len(anchors) > _MAX_LINKS:
            warnings.append("SecretHunter email: link limit exceeded; message skipped.")
            continue
        candidates: dict[str, list[Job]] = {}
        message_links: set[str] = set()
        for anchor in anchors:
            url = canonical_secrethunter_job_url(str(anchor["href"]))
            if url:
                discovered.add(url)
                message_links.add(url)
                job = _card_job(anchor, url)
                if job:
                    candidates.setdefault(url, []).append(job)
        for raw in re.findall(r'https://[^\s<>"\']+', message.body_text):
            if url := canonical_secrethunter_job_url(raw):
                discovered.add(url)
                message_links.add(url)
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or script.get_text())
            except (ValueError, TypeError, RecursionError):
                warnings.append(_UNSUPPORTED)
                continue
            for posting in _postings(payload):
                url = canonical_secrethunter_job_url(str(posting.get("url", "")))
                if not url:
                    warnings.append(_UNSUPPORTED)
                    continue
                discovered.add(url)
                message_links.add(url)
                organization = posting.get("hiringOrganization")
                company = organization.get("name") if isinstance(organization, dict) else None
                description = _text(posting.get("description"))
                job = _make_job(
                    url,
                    _text(posting.get("title")),
                    _text(company),
                    BeautifulSoup(description, "html.parser").get_text(" ", strip=True),
                    _location(posting),
                )
                if job:
                    candidates.setdefault(url, []).append(job)
                else:
                    warnings.append(_UNSUPPORTED)
        for url, evidence in candidates.items():
            if url in ambiguous:
                continue
            identities = {(job.title, job.company) for job in evidence}
            if len(identities) != 1:
                ambiguous.add(url)
                jobs.pop(url, None)
                warnings.append(_UNSUPPORTED)
                continue
            if url in jobs and (jobs[url].title, jobs[url].company) not in identities:
                ambiguous.add(url)
                jobs.pop(url, None)
                warnings.append(_UNSUPPORTED)
                continue
            jobs.setdefault(url, evidence[0])
        if any(url not in jobs for url in message_links) or (
            not candidates
            and not re.search(r"welcome|ברוכים הבאים", message.subject, re.IGNORECASE)
            and soup.get_text(" ", strip=True)
            and not (anchors and all(_account_link(str(anchor["href"])) for anchor in anchors))
        ):
            warnings.append(_UNSUPPORTED)
    return SecretHunterEmailCollection(list(jobs.values()), inspected, len(discovered), warnings)


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _account_link(value: str) -> bool:
    """Recognize non-job account mail only to avoid unsupported-template noise."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.netloc == "secrethunter.firebaseapp.com" and parsed.path.startswith("/__/auth/")
    ) or (
        parsed.netloc == "secrethunter.io"
        and parsed.path in {"/signin", "/profile", "/unsubscribe", "/upgrade"}
    )


def _make_job(
    url: str,
    title: str,
    company: str,
    description: str,
    location: str | None,
) -> Job | None:
    if not title or not company or not description:
        return None
    return Job(
        source="secrethunter_email",
        source_url=url,
        title=title,
        company=company,
        description=description,
        location=location,
        required_skills=extract_required_skills(title, description),
        years_experience=extract_years_experience(description),
    )


def _card_job(anchor: Tag, url: str) -> Job | None:
    node: Tag | None = anchor
    for _ in range(8):
        if node is None or node.name in {"body", "html", "[document]"}:
            break
        # Never climb into a container spanning different selected jobs.
        links = node.find_all("a", href=True)
        urls = {canonical_secrethunter_job_url(str(link["href"])) for link in links} - {None}
        if urls - {url}:
            return None
        titles = {
            _text(item["data-title"])
            for item in [node, *node.select("[data-title]")]
            if item.has_attr("data-title")
        }
        companies = {
            _text(item["data-company"])
            for item in [node, *node.select("[data-company]")]
            if item.has_attr("data-company")
        }
        headings = node.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        if not titles:
            titles = {_text(item.get_text(" ", strip=True)) for item in headings}
        lines = node.get_text("\n", strip=True).splitlines()
        if not companies:
            companies = {
                match[1].strip()
                for line in lines
                if (match := re.fullmatch(r"Company:\s*(.+)", line, re.IGNORECASE))
            }
        if titles and companies:
            if len(titles) != 1 or len(companies) != 1 or len(headings) > 1:
                return None
            locations = {
                _text(item["data-location"])
                for item in [node, *node.select("[data-location]")]
                if item.has_attr("data-location")
            }
            locations.update(
                match[1].strip()
                for line in lines
                if (match := re.fullmatch(r"Location:\s*(.+)", line, re.IGNORECASE))
            )
            if len(locations) > 1:
                return None
            title, company = next(iter(titles)), next(iter(companies))
            description = " ".join([title, company, node.get_text(" ", strip=True)])
            return _make_job(url, title, company, description, next(iter(locations), None))
        if node.name in {"article", "td"}:
            break
        node = node.parent if isinstance(node.parent, Tag) else None
    return None


def _postings(payload: Any) -> Iterable[dict[str, Any]]:
    pending = [payload]
    for _ in range(2000):
        if not pending:
            break
        item = pending.pop()
        if isinstance(item, dict):
            kind = item.get("@type")
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                yield item
            graph = item.get("@graph")
            if isinstance(graph, list):
                pending.extend(graph)
        elif isinstance(item, list):
            pending.extend(item)


def _location(posting: dict[str, Any]) -> str | None:
    location = posting.get("jobLocation")
    if not isinstance(location, dict):
        return None
    address = location.get("address")
    if isinstance(address, str):
        return _text(address) or None
    if not isinstance(address, dict):
        return None
    parts = [
        _text(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry")
    ]
    return ", ".join(part for part in parts if part) or None
