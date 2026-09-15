"""Explicit capability registry for supported applicant tracking systems."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class PortalAdapter:
    name: str
    host_markers: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    confirmation_markers: tuple[str, ...]

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").casefold()
        return any(marker in host for marker in self.host_markers)


ADAPTERS = (
    PortalAdapter(
        "greenhouse",
        ("greenhouse.io",),
        ("#submit_app", 'button[type="submit"]', 'input[type="submit"]'),
        ("application received", "application submitted", "thank you for applying"),
    ),
    PortalAdapter(
        "comeet",
        ("comeet.com",),
        ('form button[type="submit"]', 'form input[type="submit"]'),
        ("application received", "application was sent", "thank you for applying"),
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


def adapter_for_url(url: str) -> PortalAdapter | None:
    return next((adapter for adapter in ADAPTERS if adapter.matches(url)), None)
