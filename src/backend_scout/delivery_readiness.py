"""Conservative portal readiness facts, not delivery authorization or match scores."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from backend_scout.application_urls import application_entry_url
from backend_scout.models import Job
from backend_scout.portal_adapters import adapter_for_url

ReadinessState = Literal[
    "supported_after_approval",
    "needs_input",
    "unverified",
    "manual_or_unsupported",
    "unavailable",
]

_LABELS: dict[ReadinessState, str] = {
    "supported_after_approval": "Supported after approval",
    "needs_input": "Needs input",
    "unverified": "Unverified",
    "manual_or_unsupported": "Manual or unsupported",
    "unavailable": "Unavailable",
}
_ACTIVE_STATUSES = frozenset(
    {
        "discovered",
        "shortlisted",
        "enriching",
        "drafting",
        "review_ready",
        "approved",
        "preparing",
        "needs_input",
        "revision_requested",
        "blocked",
        "found",
        "digest_sent",
        "approved_to_tailor",
        "cv_drafted",
        "approved_to_submit",
    }
)
_UNRESOLVED_INPUT_CODES = frozenset({"external_receipt_check", "legacy_portal_incomplete"})
_POSTING_PLACEHOLDERS = frozenset(
    {
        "published lin-srael job listing.",
        "published public ats job posting.",
        "indeed email recommendation.",
    }
)
_INSPECTION_MAX_AGE = timedelta(hours=24)
POSTING_BLOCK_CODES = frozenset({
    "posting_unavailable", "posting_identity_mismatch", "posting_url_invalid",
    "posting_redirect_unverified",
})


def posting_observation_blocker(observation: dict | None) -> dict | None:
    """A negative route observation is not proof the entire vacancy is closed."""
    if not isinstance(observation, dict) or observation.get("status") != "unavailable":
        return None
    reasons = {
        "closed": ("posting_unavailable", "The saved application link is no longer available publicly. Nothing was sent. Recheck or update the official URL; your existing CV is still available."),
        "identity_mismatch": ("posting_identity_mismatch", "The destination does not match this job. Nothing was sent. Verify the official application URL."),
        "unsafe_url": ("posting_url_invalid", "The saved destination is not a safe public posting URL. Nothing was sent. Verify the official application URL."),
        "redirect_blocked": ("posting_redirect_unverified", "The saved link redirects away from the verified posting. Nothing was sent. Verify the official application URL."),
    }
    code = observation.get("reason_code")
    match = reasons.get(code) if isinstance(code, str) else None
    return {"reason_code": match[0], "message": match[1]} if match else None


class DeliveryReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ReadinessState
    label: str
    reasons: list[str]
    adapter: str | None = None
    route: Literal["portal"] = "portal"
    checked_at: str | None = None


def _result(
    state: ReadinessState,
    reasons: list[str],
    adapter: str | None = None,
    checked_at: str | None = None,
) -> DeliveryReadiness:
    return DeliveryReadiness(
        state=state,
        label=_LABELS[state],
        reasons=reasons,
        adapter=adapter,
        checked_at=checked_at,
    )


def _official_url(job: Job) -> str | None:
    # The discovery URL is not evidence of an official application destination.
    url = application_entry_url(job.application_url)
    if url is None:
        return None
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or any(character.isspace() for character in url):
        return None
    return url


def assess_delivery_readiness(
    job: Job,
    *,
    lifecycle_status: str,
    has_cv: bool,
    package_ready: bool,
    needs_input_code: str | None = None,
    inspection: dict | None = None,
    package_fingerprint: str | None = None,
    verified_route: dict | None = None,
    tracker: str = "production",
) -> DeliveryReadiness:
    """Classify supplied facts without I/O, persistence, or approval decisions.

    Inspection keys are ``kind='browser_form'``, ``passed=True`` (strict bool),
    ``application_url``, ``package_fingerprint``, ``adapter``, ``blockers=[]``,
    and ``checked_at`` (ISO-8601 UTC, no future time, at most 24 hours old).
    Bindings use exact equality. Missing/malformed inspection facts fail closed.
    Freshness applies only to the inspection, never to approval expiry. Callers
    must supply the actual current CV/package facts; this function verifies no files.
    Reasons are stable codes, and checked_at is set only for a valid inspection.
    """
    # An allowlist also excludes new/unknown lifecycle values until reviewed.
    if lifecycle_status not in _ACTIVE_STATUSES:
        return _result("unavailable", ["lifecycle_unavailable"])
    if needs_input_code in _UNRESOLVED_INPUT_CODES:
        return _result("unavailable", ["delivery_unresolved"])

    url = _official_url(job)
    adapter = adapter_for_url(url, tracker="production") if url else None
    adapter_name = adapter.name if adapter else None
    if verified_route:
        from backend_scout.application_routes import ApplicationRoute

        try:
            route = ApplicationRoute.model_validate(verified_route)
            if ((route.company, route.title) == (job.company, job.title)
                and route.status == "verified" and route.application_url == url
                and url in route.evidence_urls):
                adapter_name = route.adapter_name or "employer-form"
        except ValueError:
            pass
    if needs_input_code in POSTING_BLOCK_CODES:
        return _result("unavailable", [needs_input_code], adapter_name)
    missing = []
    if url is None:
        missing.append("official_route_required")
    posting = job.description.strip().casefold()
    if len(posting) < 60 or posting in _POSTING_PLACEHOLDERS or posting == job.title.casefold():
        missing.append("full_posting_required")
    if has_cv is not True:
        missing.append("cv_required")
    if package_ready is not True:
        missing.append("package_required")
    if needs_input_code:
        missing.append(needs_input_code)
    elif lifecycle_status in {"needs_input", "blocked"}:
        missing.append("input_required")
    if missing:
        return _result("needs_input", missing, adapter_name)

    if adapter_name is None or adapter_name == "linkedin-easy-apply":
        return _result("manual_or_unsupported", ["manual_or_unsupported_portal"], adapter_name)
    if inspection is None:
        return _result("unverified", ["inspection_required"], adapter_name)
    if not isinstance(inspection, dict):
        return _result("unverified", ["inspection_malformed"], adapter_name)

    if inspection.get("kind") != "browser_form" or inspection.get("passed") is not True:
        return _result("unverified", ["browser_form_inspection_required"], adapter_name)
    if inspection.get("application_url") != url:
        return _result("unverified", ["inspection_url_mismatch"], adapter_name)
    if (
        not isinstance(package_fingerprint, str)
        or not package_fingerprint.strip()
        or inspection.get("package_fingerprint") != package_fingerprint
    ):
        return _result("unverified", ["inspection_package_mismatch"], adapter_name)
    if inspection.get("adapter") != adapter_name:
        return _result("unverified", ["inspection_adapter_mismatch"], adapter_name)
    blockers = inspection.get("blockers")
    if not isinstance(blockers, list) or blockers:
        return _result("unverified", ["inspection_blockers_unverified"], adapter_name)

    timestamp = inspection.get("checked_at")
    if not isinstance(timestamp, str):
        return _result("unverified", ["inspection_timestamp_invalid"], adapter_name)
    try:
        checked_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return _result("unverified", ["inspection_timestamp_invalid"], adapter_name)
    if checked_at.tzinfo is None or checked_at.utcoffset() != timedelta(0):
        return _result("unverified", ["inspection_timestamp_invalid"], adapter_name)
    age = datetime.now(UTC) - checked_at
    if not timedelta(0) <= age <= _INSPECTION_MAX_AGE:
        return _result("unverified", ["inspection_not_current"], adapter_name)

    return _result(
        "supported_after_approval",
        ["current_browser_form_inspection", "approval_required"],
        adapter_name,
        checked_at.isoformat(),
    )
