"""Keep reusable application entry links separate from transient sign-in pages."""

import re
from urllib.parse import parse_qs, urlsplit

AUTHENTICATION_PATH = re.compile(
    r"/(?:login|signin|sign-in|signup|sign-up|authwall|checkpoint|uas)(?:/|$)", re.IGNORECASE
)


def is_authentication_url(url: str) -> bool:
    return bool(AUTHENTICATION_PATH.search(urlsplit(url).path))


def application_entry_url(application_url: str | None, source_url: str | None = None) -> str | None:
    for url in (application_url, source_url):
        if not url:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
            if is_authentication_url(url):
                continue
            if {key.casefold() for key in parse_qs(parsed.query)} & {
                "password", "access_token", "id_token", "token", "code",
            }:
                continue
        except ValueError:
            continue
        return url
    return None
