"""Observe a public Greenhouse form, never prepare or submit an application.

Contract: https://docs.greenhouse.io/job-board.html#retrieve-a-job
Only the documented, unauthenticated job GET with ``questions=true`` is used.
The existing enrichment reader owns URL/response validation and fetch budgets.
An injected ``fetcher(url) -> httpx.Response`` must perform GET without redirects
or credentials; injected streaming responses are closed by the shared reader.

Each output entry represents one field: ``key`` is its original name, ``kind``
its original type, and ``options`` its ordered display labels, NOT answer IDs.
``label`` and the actual boolean ``required`` describe the containing question.
Multiple fields under one required question can be alternatives (e.g. resume
file OR text); repeating that flag does not make every alternative mandatory.
Unknown/conditional schemas are unavailable, with no misleading partial result.
Even an inspected schema is not proof of completeness or autonomous readiness:
hosted forms may impose additional education, consent or conditional questions.
There is no candidate-data, attachment, authentication or submission interface.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from backend_scout import job_enrichment as enrichment
from backend_scout.models import Job

MAX_QUESTIONS = 100
MAX_LABEL_CHARS = 2_000
MAX_VALUE_CHARS = 2_000
MAX_KEY_CHARS = 200
MAX_OPTIONS = 200
_GROUPS = ("questions", "location_questions", "compliance", "education_questions")
_KINDS = {
    "input_file",
    "input_text",
    "input_hidden",
    "textarea",
    "multi_value_single_select",
    "multi_value_multi_select",
}
_OBSERVATION = (
    "Observed public schema only; completeness of education, consent and conditional "
    "requirements is unknown. This is not approval or autonomous submission readiness."
)


@dataclass(frozen=True)
class GreenhouseFormInspection:
    status: Literal["inspected", "unavailable"]
    reason_code: str
    message: str
    source_url: str | None
    checked_at: str
    questions: tuple[dict, ...] = ()


def _unknown() -> None:
    raise enrichment._Unavailable(
        "unknown_schema", "The public form schema is missing, malformed or unsupported."
    )


def _string(value: Any, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 and char not in "\t\r\n" or ord(char) == 127 for char in value)
    ):
        _unknown()
    return value


def _options(field: dict) -> list[str]:
    if "values" not in field and field["type"].startswith("multi_value_"):
        _unknown()
    values = field.get("values", [])
    if not isinstance(values, list) or len(values) > MAX_OPTIONS:
        _unknown()
    labels = []
    for option in values:
        if not isinstance(option, dict) or set(option) != {"value", "label"}:
            _unknown()
        value = option["value"]
        if type(value) is int:
            if len(str(value)) > MAX_VALUE_CHARS:
                _unknown()
        elif isinstance(value, str):
            _string(value, MAX_VALUE_CHARS)
        else:
            _unknown()
        labels.append(_string(option["label"], MAX_LABEL_CHARS))
    return labels


def _questions(payload: dict) -> tuple[dict, ...]:
    if "questions" not in payload:
        _unknown()
    # These groups cannot be faithfully represented by the flat field contract.
    # In particular, GDPR flags and demographic free-form options are not fields.
    if "data_compliance" in payload and payload["data_compliance"] != []:
        _unknown()
    if "demographic_questions" in payload:
        group = payload["demographic_questions"]
        if not isinstance(group, dict) or group.get("questions") != []:
            _unknown()
    for name in payload:
        if (
            name.endswith("_questions")
            and name not in (*_GROUPS, "demographic_questions")
            or name.startswith(("education", "employment"))
            and name != "education_questions"
        ):
            _unknown()

    result: list[dict] = []
    keys: set[str] = set()
    count = 0
    for name in _GROUPS:
        if name not in payload:
            continue
        group = payload[name]
        if not isinstance(group, list):
            _unknown()
        count += len(group)
        if count > MAX_QUESTIONS:
            _unknown()
        for question in group:
            if not isinstance(question, dict) or set(question) != {"label", "required", "fields"}:
                _unknown()
            label = _string(question["label"], MAX_LABEL_CHARS)
            required = question["required"]
            if type(required) is not bool:
                _unknown()
            fields = question["fields"]
            if not isinstance(fields, list) or not fields or len(fields) > MAX_QUESTIONS:
                _unknown()
            for field in fields:
                if not isinstance(field, dict) or not {"name", "type"} <= set(field) <= {
                    "name",
                    "type",
                    "values",
                }:
                    _unknown()
                key = _string(field["name"], MAX_KEY_CHARS)
                kind = _string(field["type"], MAX_KEY_CHARS)
                if key in keys or kind not in _KINDS or len(result) >= MAX_QUESTIONS:
                    _unknown()
                keys.add(key)
                result.append(
                    {
                        "key": key,
                        "label": label,
                        "required": required,
                        "kind": kind,
                        "options": _options(field),
                    }
                )
    return tuple(result)


def _form(
    job: Job, posting: enrichment._Posting, body: bytes, content_type: str
) -> tuple[dict, ...]:
    if "json" not in content_type:
        text = enrichment._plain(body.decode("utf-8", errors="replace"))
        if enrichment._CLOSED.search(text):
            raise enrichment._Unavailable("closed", "The public posting says the role is closed.")
        raise enrichment._Unavailable("source_gated", "The source did not expose public JSON.")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise enrichment._Unavailable(
            "invalid_response", "The source returned invalid JSON."
        ) from None
    if not isinstance(payload, dict):
        raise enrichment._Unavailable("invalid_response", "The source returned no job object.")
    identifier = payload.get("id")
    if type(identifier) is not int or identifier != int(posting.job_id):
        raise enrichment._Unavailable("identity_mismatch", "The public posting ID does not match.")
    title, company = payload.get("title"), payload.get("company_name")
    if any(not isinstance(value, str) or not value.strip() for value in (title, company)):
        raise enrichment._Unavailable(
            "invalid_response", "The job title or company is unverifiable."
        )
    enrichment._identity(job, title, company)
    for name in ("content", "message", "error"):
        if isinstance(payload.get(name), str):
            text = enrichment._plain(payload[name])
            if enrichment._CLOSED.search(text):
                raise enrichment._Unavailable(
                    "closed", "The public posting says the role is closed."
                )
            if enrichment._GATED.search(text):
                raise enrichment._Unavailable("source_gated", "The source requires verification.")
    return _questions(payload)


def _target(job: Job) -> enrichment._Posting:
    selected = None
    failure = enrichment._Unavailable(
        "unsupported_url", "No exact public Greenhouse URL is available."
    )
    # Discovery remains provenance after an owner reviews a replacement destination.
    # Never validate a broken official URL by silently inspecting its old source.
    targets = (job.application_url,) if job.application_url else (job.source_url,)
    for url in targets:
        try:
            posting = enrichment._posting(url)
        except enrichment._Unavailable as exc:
            failure = exc
            continue
        if posting is not None and posting.provider == "greenhouse":
            selected = posting
    if selected is None:
        raise failure
    return selected


def inspect_greenhouse_form(
    job: Job, *, fetcher: enrichment.Fetcher | None = None
) -> GreenhouseFormInspection:
    """GET one exact public job form without changing ``job`` or any workflow.

    ``source_url`` is the safe API URL attempted, or None when input validation
    prevents a request. ``checked_at`` is an aware UTC ISO timestamp. Unavailable
    results never include a partial question list. Missing/non-boolean required
    flags are unknown schema, never coerced or silently defaulted to optional.
    """
    source_url = None
    try:
        posting = _target(job)
        api_url = enrichment._api_url(posting)
        assert api_url is not None
        source_url = str(httpx.URL(api_url).copy_add_param("questions", "true"))
        with ExitStack() as stack:
            if fetcher is None:
                client = stack.enter_context(
                    httpx.Client(
                        timeout=enrichment.TIMEOUT_SECONDS,
                        follow_redirects=False,
                        trust_env=False,
                        headers={"User-Agent": "BackendScout/0.1", "Accept": "application/json"},
                    )
                )

                def fetcher(url: str) -> httpx.Response:
                    return client.send(
                        client.build_request("GET", url), stream=True, follow_redirects=False
                    )

            reader = enrichment._Reader(fetcher)
            body, content_type, _ = reader.read(source_url, posting)
            questions = _form(job, posting, body, content_type)
        return GreenhouseFormInspection(
            "inspected",
            "public_schema_inspected",
            _OBSERVATION,
            source_url,
            datetime.now(UTC).isoformat(),
            questions,
        )
    except enrichment._Unavailable as exc:
        code, message = exc.code, exc.message
    except httpx.TimeoutException:
        code, message = "fetch_timeout", "The bounded public form fetch timed out."
    except (httpx.HTTPError, OSError):
        code, message = "fetch_failed", "The public form could not be fetched."
    return GreenhouseFormInspection(
        "unavailable", code, f"{message} {_OBSERVATION}", source_url, datetime.now(UTC).isoformat()
    )
