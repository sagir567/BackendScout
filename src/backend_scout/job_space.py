"""Read-only transport for the documented Job Space search endpoint."""

import json
from collections.abc import Mapping

import httpx

from backend_scout.secrets import KeyringCredentialStore

JOB_SPACE_ENDPOINT = "https://jobs.ti-space.com/api/v1/jobs"
JOB_SPACE_CREDENTIAL = KeyringCredentialStore("BackendScout.JobSpace", "default")
_MAX_BYTES = 2_000_000


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
