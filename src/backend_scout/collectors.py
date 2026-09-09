"""Policy-safe collectors for public company ATS job-board APIs."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from backend_scout.models import Job
from backend_scout.targets import AtsProvider, TargetCompany

JsonFetcher = Callable[[str], Any]
ISRAEL_LOCATION_TERMS = (
    "israel",
    "tel aviv",
    "tel-aviv",
    "herzliya",
    "haifa",
    "jerusalem",
    "raanana",
    "netanya",
    "petah tikva",
    "rishon",
    "beer sheva",
)
REMOTE_TERMS = ("remote", "work from home", "distributed")
SKILL_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Python", ("python",)),
    ("FastAPI", ("fastapi",)),
    ("Django", ("django",)),
    ("Flask", ("flask",)),
    ("C++", ("c++", "cpp", "c plus plus")),
    ("C#", ("c#", "c sharp")),
    (".NET", ("net", "dotnet", "asp net", "asp net core")),
    ("Java", ("java",)),
    ("SQL", ("sql",)),
    ("PostgreSQL", ("postgresql", "postgres", "psql")),
    ("MongoDB", ("mongodb", "mongo db", "mongo")),
    ("Redis", ("redis",)),
    ("Docker", ("docker", "container", "containers", "containerization")),
    ("Kubernetes", ("kubernetes", "k8s")),
    ("Azure", ("azure", "azure cloud")),
    ("AWS", ("aws", "amazon web services")),
    ("GCP", ("gcp", "google cloud", "google cloud platform")),
    ("GCS", ("gcs", "google cloud storage")),
    ("Linux", ("linux", "unix")),
    ("APIs", ("api", "apis", "rest api", "restful api")),
    ("GraphQL", ("graphql",)),
    ("Microservices", ("microservices", "microservice")),
    ("OOP", ("oop", "object oriented", "object-oriented")),
    ("CI/CD", ("ci/cd", "cicd", "continuous integration", "continuous delivery")),
    ("Git", ("git", "github")),
    (
        "Multithreading",
        ("multithreading", "multithreaded", "multi threading", "multi-threaded", "concurrency", "threading"),
    ),
    ("Data Pipelines", ("data pipeline", "data pipelines", "etl")),
    ("Distributed Systems", ("distributed systems", "distributed system")),
    ("Performance", ("performance optimization", "performance-oriented", "performance oriented")),
    ("Large Codebase", ("large codebase", "large code base", "complex codebase", "complex code base")),
)


def collect_public_jobs(target: TargetCompany, fetch_json: JsonFetcher | None = None) -> list[Job]:
    """Fetch published jobs from one configured company board; no apply API is used."""
    fetch = fetch_json or _fetch_json
    if target.provider == AtsProvider.GREENHOUSE:
        return parse_greenhouse_jobs(target, fetch(_greenhouse_url(target.board_token)))
    if target.provider == AtsProvider.LEVER:
        return parse_lever_jobs(target, fetch(_lever_url(target.board_token)))
    if target.provider == AtsProvider.ASHBY:
        return parse_ashby_jobs(target, fetch(_ashby_url(target.board_token)))
    raise ValueError(f"Unsupported ATS provider: {target.provider.value}")


def is_israel_or_remote(job: Job) -> bool:
    location = " ".join(filter(None, [job.location, job.remote_policy])).casefold()
    return any(term in location for term in ISRAEL_LOCATION_TERMS + REMOTE_TERMS)


def parse_greenhouse_jobs(target: TargetCompany, payload: Any) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise TypeError("Greenhouse response does not contain a jobs list")
    jobs: list[Job] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        url = _text(item.get("absolute_url"))
        if not title or not url:
            continue
        location = _text((item.get("location") or {}).get("name"))
        content = _html_to_text(_text(item.get("content")) or "")
        jobs.append(_job(target, title, url, location, content))
    return jobs


def parse_lever_jobs(target: TargetCompany, payload: Any) -> list[Job]:
    if not isinstance(payload, list):
        raise TypeError("Lever response does not contain a job list")
    jobs: list[Job] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("text"))
        url = _text(item.get("hostedUrl") or item.get("applyUrl"))
        if not title or not url:
            continue
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        location = _text(categories.get("location"))
        commitment = _text(categories.get("commitment"))
        content = _text(item.get("descriptionPlain")) or _html_to_text(_text(item.get("description")) or "")
        jobs.append(_job(target, title, url, location, content, commitment))
    return jobs


def parse_ashby_jobs(target: TargetCompany, payload: Any) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise TypeError("Ashby response does not contain a jobs list")
    jobs: list[Job] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        title = _text(item.get("title"))
        url = _text(item.get("jobUrl"))
        if not title or not url:
            continue
        location = _text(item.get("location"))
        content = _text(item.get("descriptionPlain")) or _html_to_text(_text(item.get("descriptionHtml")) or "")
        jobs.append(
            _job(
                target,
                title,
                url,
                location,
                content,
                _text(item.get("employmentType")),
                _text(item.get("workplaceType")),
                _salary_text(item.get("compensation")),
            )
        )
    return jobs


def _job(
    target: TargetCompany,
    title: str,
    url: str,
    location: str | None,
    description: str,
    employment_type: str | None = None,
    remote_policy: str | None = None,
    salary_text: str | None = None,
) -> Job:
    skills = _extract_required_skills(title, description)
    return Job(
        source=f"{target.provider.value}:{target.name}",
        source_url=url,
        company=target.name,
        title=title,
        location=location,
        employment_type=employment_type,
        remote_policy=remote_policy,
        salary_text=salary_text,
        description=description or "Published public ATS job posting.",
        required_skills=skills,
        years_experience=_extract_years_experience(description),
        discovered_at=datetime.now(UTC),
    )


def _greenhouse_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def _lever_url(token: str) -> str:
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def _ashby_url(token: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def _fetch_json(url: str) -> Any:
    import httpx

    response = httpx.get(url, timeout=30.0, headers={"User-Agent": "BackendScout/0.1"})
    response.raise_for_status()
    return response.json()


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _html_to_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _salary_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    minimum, maximum, currency = value.get("minValue"), value.get("maxValue"), value.get("currencyCode")
    if isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)):
        return f"{minimum:g}-{maximum:g} {currency or ''}".strip()
    return None


def _extract_required_skills(title: str, description: str) -> list[str]:
    searchable_text = " ".join(filter(None, [title, description]))
    normalized_text = f" {_normalize_for_skill_search(searchable_text)} "
    found: list[str] = []
    for label, aliases in SKILL_KEYWORDS:
        if any(_contains_normalized_phrase(normalized_text, alias) for alias in aliases):
            found.append(label)
    return found


def _contains_normalized_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_for_skill_search(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in normalized_text


def _normalize_for_skill_search(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", value.casefold().replace(".net", "dotnet")))


def _extract_years_experience(description: str) -> str | None:
    patterns = (
        r"(\d+)\s*\+\s*(?:years?|yrs?)",
        r"(\d+)\s*(?:-\s*\d+\s*)?(?:years?|yrs?)\s+(?:of\s+)?(?:relevant\s+)?experience",
        r"at least\s+(\d+)\s+(?:years?|yrs?)",
        r"minimum\s+of\s+(\d+)\s+(?:years?|yrs?)",
    )
    for pattern in patterns:
        match = re.search(pattern, description.casefold())
        if match:
            return f"{match.group(1)}+ years"
    return None
