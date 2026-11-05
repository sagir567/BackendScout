"""Explicit capability registry for supported applicant tracking systems."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from backend_scout.config import TrackerName


@dataclass(frozen=True)
class PortalAdapter:
    name: str
    host_markers: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    confirmation_markers: tuple[str, ...]

    def matches(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").casefold().rstrip(".")
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                return False
            _ = parsed.port
        except ValueError:
            return False
        return any(host == marker or host.endswith(f".{marker}") for marker in self.host_markers)


ADAPTERS = (
    PortalAdapter(
        "ashby",
        ("ashbyhq.com",),
        ('form button[type="submit"]', 'form input[type="submit"]'),
        ("application submitted", "application received", "thank you for applying", "thanks for applying"),
    ),
    PortalAdapter(
        "greenhouse",
        ("greenhouse.io",),
        ("#submit_app", 'button[type="submit"]', 'input[type="submit"]'),
        ("application received", "application submitted", "thank you for applying"),
    ),
    PortalAdapter(
        "comeet",
        ("comeet.com", "comeet.co"),
        ('form button[type="submit"]', 'form input[type="submit"]'),
        ("application received", "application was sent", "thank you for applying", "your application has been submitted"),
    ),
    PortalAdapter(
        "lever",
        ("lever.co",),
        ('form button[type="submit"]', 'form input[type="submit"]'),
        ("application submitted", "thanks for applying", "thank you for applying"),
    ),
    PortalAdapter(
        "linkedin-easy-apply",
        ("linkedin.com",),
        (
            'button[aria-label="Submit application"]',
            'button[aria-label="שליחת הבקשה"]',
        ),
        (
            "application was sent",
            "application submitted",
            "your application was sent",
            "הבקשה נשלחה",
        ),
    ),
    PortalAdapter(
        "test-fixture",
        ("example.com", "127.0.0.1", "localhost"),
        ('button[type="submit"]', 'input[type="submit"]'),
        ("application received", "application submitted", "thank you for applying"),
    ),
)


def adapter_for_url(
    url: str, *, tracker: TrackerName | str | None = None
) -> PortalAdapter | None:
    """Match exact/subdomain hosts; production can never select the test fixture.

    The optional tracker preserves the legacy inspection API. Package delivery
    must always supply its package tracker, including after redirects.
    """
    production = tracker is not None and TrackerName(tracker) == TrackerName.PRODUCTION
    return next(
        (
            adapter for adapter in ADAPTERS
            if adapter.matches(url) and not (production and adapter.name == "test-fixture")
        ),
        None,
    )
