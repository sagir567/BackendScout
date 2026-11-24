"""Front2Job employer-directory discovery for supported public ATS boards."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from backend_scout.collectors import collect_public_jobs
from backend_scout.models import Job
from backend_scout.targets import AtsProvider, TargetCompany

FRONT2JOB_EMPLOYERS_URL = "https://front2job.com/employers"
FetchText = Callable[[str], str]


@dataclass(frozen=True)
class Front2JobEmployer:
    name: str
    careers_url: str


@dataclass(frozen=True)
class Front2JobCollection:
    jobs: list[Job]
    employers_checked: int
    supported_boards: int
    unsupported_employers: tuple[str, ...]
    warnings: tuple[str, ...]


def parse_front2job_employers(html: str) -> list[Front2JobEmployer]:
    """Read only the external employer links from the verified directory page."""
    soup = BeautifulSoup(html, "html.parser")
    employers: list[Front2JobEmployer] = []
    seen: set[str] = set()
    excluded_hosts = {
        "front2job.com", "www.front2job.com", "linkedin.com", "www.linkedin.com",
        "facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com",
    }
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        name = anchor.get_text(" ", strip=True)
        if not name or not href.startswith(("https://", "http://")):
            continue
        host = (urlparse(href).hostname or "").casefold()
        if not host or host in excluded_hosts or href in seen:
            continue
        seen.add(href)
        employers.append(Front2JobEmployer(name=name, careers_url=href))
    return employers


def parse_front2job_bundle(javascript: str) -> list[Front2JobEmployer]:
    """Extract the public employer array from Front2Job's rendered app bundle."""
    pattern = re.compile(
        r'\{name:"((?:\\.|[^"\\])*)",logo:[^,{}]+,url:"((?:\\.|[^"\\])*)"\}'
    )
    employers: list[Front2JobEmployer] = []
    seen: set[str] = set()
    for raw_name, raw_url in pattern.findall(javascript):
        try:
            name = json.loads(f'"{raw_name}"')
            url = json.loads(f'"{raw_url}"')
        except json.JSONDecodeError:
            continue
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if not name.strip() or not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        employers.append(Front2JobEmployer(name=name.strip(), careers_url=url))
    return employers


def front2job_bundle_urls(html: str) -> list[str]:
    return [
        urljoin(FRONT2JOB_EMPLOYERS_URL, str(script.get("src")))
        for script in BeautifulSoup(html, "html.parser").select("script[src]")
        if "/assets/" in str(script.get("src") or "")
    ]


def ats_targets_from_page(employer: Front2JobEmployer, html: str = "") -> list[TargetCompany]:
    """Discover supported public job boards without guessing company identifiers."""
    candidates = [employer.careers_url]
    if html:
        candidates.extend(
            str(anchor.get("href") or "")
            for anchor in BeautifulSoup(html, "html.parser").select("a[href]")
        )
    targets: list[TargetCompany] = []
    seen: set[tuple[AtsProvider, str]] = set()
    patterns = (
        (AtsProvider.GREENHOUSE, re.compile(r"https?://(?:job-boards\.)?greenhouse\.io/([^/?#]+)", re.IGNORECASE)),
        (AtsProvider.LEVER, re.compile(r"https?://jobs\.lever\.co/([^/?#]+)", re.IGNORECASE)),
        (AtsProvider.ASHBY, re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", re.IGNORECASE)),
    )
    for candidate in candidates:
        for provider, pattern in patterns:
            match = pattern.search(candidate)
            if not match:
                continue
            key = (provider, match.group(1))
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                TargetCompany(
                    name=employer.name,
                    provider=provider,
                    board_token=match.group(1),
                    notes=f"Discovered from {FRONT2JOB_EMPLOYERS_URL}",
                )
            )
    return targets


def collect_front2job_jobs(
    fetch_text: FetchText | None = None,
    *,
    max_workers: int = 6,
) -> Front2JobCollection:
    """Synchronize Front2Job and collect jobs only through verified public ATS APIs."""
    fetch = fetch_text or _fetch_text
    directory_html = fetch(FRONT2JOB_EMPLOYERS_URL)
    employers = parse_front2job_employers(directory_html)
    if not employers:
        for bundle_url in front2job_bundle_urls(directory_html):
            employers = parse_front2job_bundle(fetch(bundle_url))
            if employers:
                break
    if not employers:
        raise ValueError("Front2Job employer directory rendered no employers")
    warnings: list[str] = []
    pages: dict[str, str] = {}

    def load(employer: Front2JobEmployer) -> tuple[str, str]:
        return employer.careers_url, fetch(employer.careers_url)

    indirect = [employer for employer in employers if not ats_targets_from_page(employer)]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(load, employer): employer for employer in indirect}
        for future in as_completed(futures):
            employer = futures[future]
            try:
                url, html = future.result()
                pages[url] = html
            except (httpx.HTTPError, OSError, ValueError) as exc:
                warnings.append(f"{employer.name}: careers page unavailable ({type(exc).__name__})")

    jobs: list[Job] = []
    unsupported: list[str] = []
    supported_boards = 0
    seen_boards: set[tuple[AtsProvider, str]] = set()
    for employer in employers:
        targets = ats_targets_from_page(employer, pages.get(employer.careers_url, ""))
        if not targets:
            unsupported.append(employer.name)
            continue
        for target in targets:
            key = (target.provider, target.board_token)
            if key in seen_boards:
                continue
            seen_boards.add(key)
            supported_boards += 1
            try:
                jobs.extend(collect_public_jobs(target))
            except (httpx.HTTPError, TypeError, ValueError) as exc:
                warnings.append(f"{employer.name}: ATS unavailable ({type(exc).__name__})")
    return Front2JobCollection(
        jobs=jobs,
        employers_checked=len(employers),
        supported_boards=supported_boards,
        unsupported_employers=tuple(unsupported),
        warnings=tuple(warnings),
    )


def _fetch_text(url: str) -> str:
    with httpx.Client(
        timeout=12,
        follow_redirects=True,
        headers={"User-Agent": "TechJobScout/0.1 (+https://front2job.com/employers)"},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text
