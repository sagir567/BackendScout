"""Read-only transport for the documented Job Space search endpoint."""

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from keyring.errors import KeyringError

from backend_scout.application_urls import application_entry_url
from backend_scout.models import Job
from backend_scout.secrets import KeyringCredentialStore

JOB_SPACE_ENDPOINT = "https://jobs.ti-space.com/api/v1/jobs"
JOB_SPACE_CREDENTIAL = KeyringCredentialStore("BackendScout.JobSpace", "default")
_MAX_BYTES = 2_000_000
_QUERIES = ("backend", "python", ".net", "automation", "AI engineer")


class JobSpaceError(ValueError):
    """A safe error that never includes remote response text or credentials."""


def fetch_job_space_jobs(
    client: httpx.Client, key: str, *, query: str, seniority: str = "junior",
) -> dict | list:
    if not key or len(key) > 1024 or not key.isascii() or any(c.isspace() for c in key):
        raise JobSpaceError("Invalid Job Space key format.")
    try:
        with client.stream(
            "GET", JOB_SPACE_ENDPOINT, params={"q": query, "seniority": seniority},
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.status_code in {401, 403}:
                raise JobSpaceError("Job Space rejected the credential.")
            if response.status_code == 429:
                raise JobSpaceError("Job Space rate limit reached.")
            if not response.is_success:
                raise JobSpaceError(f"Job Space returned HTTP {response.status_code}.")
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > _MAX_BYTES:
                    raise JobSpaceError("Job Space response exceeded the size limit.")
                content.extend(chunk)
    except httpx.HTTPError:
        raise JobSpaceError("Job Space could not be reached.") from None
    try:
        payload = json.loads(content)
    except (ValueError, RecursionError):
        raise JobSpaceError("Job Space did not return valid JSON.") from None
    if not isinstance(payload, (dict, list)):
        raise JobSpaceError("Job Space returned an unexpected JSON shape.")
    return payload


def job_space_response_shape(payload: dict | list) -> dict:
    """Report only sanitized field names, never job values or token material."""
    def field_names(mapping: Mapping) -> list[str]:
        return sorted(k for k in mapping if isinstance(k, str) and len(k) < 40
                      and k.isascii() and k.replace("_", "").isalnum())[:30]

    lists = payload if isinstance(payload, list) else next(
        (payload[k] for k in ("jobs", "results", "data") if isinstance(payload.get(k), list)), [],
    )
    sample = next((item for item in lists if isinstance(item, dict)), {})
    return {"root": type(payload).__name__, "fields": field_names(payload) if isinstance(payload, dict) else [],
            "job_fields": field_names(sample), "items": len(lists)}


def parse_job_space_jobs(payload: dict | list) -> list[Job]:
    rows = payload if isinstance(payload, list) else next(
        (payload[key] for key in ("jobs", "results", "data") if isinstance(payload.get(key), list)),
        None,
    )
    if rows is None:
        raise JobSpaceError("Job Space search returned an unrecognized result envelope.")

    def field(item: dict, *keys: str) -> str:
        return next((item[k].strip() for k in keys if isinstance(item.get(k), str) and item[k].strip()), "")

    jobs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = str(row.get("id", ""))
        title = field(row, "title")
        company = field(row, "company_name", "company")
        if not company and isinstance(row.get("company"), dict):
            company = field(row["company"], "name")
        if not re.fullmatch(r"[0-9]{1,18}", identifier) or not title or not company:
            continue
        candidate = field(row, "application_url", "apply_url", "source_url", "url")
        destination = application_entry_url(candidate)
        if not destination or not destination.startswith("https://"):
            continue
        parsed = urlsplit(destination)
        if (parsed.hostname == "jobs.ti-space.com"
                or re.search(r"token|secret|password|session|signature|api.?key|credential", parsed.query, re.IGNORECASE)):
            continue
        description = field(row, "description", "description_text")
        jobs.append(Job(
            source="job_space", source_url=f"https://jobs.ti-space.com/jobs/{identifier}",
            application_url=destination, company=company, title=title,
            location=field(row, "location", "city") or None,
            remote_policy=field(row, "work_mode", "remote_policy") or None,
            description=BeautifulSoup(description[:30_000], "html.parser").get_text(" ", strip=True)
            or "Job Space listing; description pending.",
        ))
    if rows and not jobs:
        raise JobSpaceError("Job Space returned no recognizable records with public employer links.")
    return jobs


def collect_job_space_jobs() -> list[Job]:
    try:
        key = JOB_SPACE_CREDENTIAL.load()
    except (KeyringError, OSError, RuntimeError):
        raise JobSpaceError("Job Space credential storage is unavailable.") from None
    if not key:
        raise JobSpaceError("Job Space key is not configured; run collect job-space-connect.")
    results = {}
    with httpx.Client(timeout=12, trust_env=False) as client:
        for query in _QUERIES:
            for job in parse_job_space_jobs(fetch_job_space_jobs(client, key, query=query)):
                results[job.source_url] = job
    return list(results.values())
