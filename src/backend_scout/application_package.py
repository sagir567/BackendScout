"""Pure, immutable review content, not an approval or permission to deliver.

Version 1 fingerprints compact, key-sorted JSON encoded as UTF-8, including null
fields and the schema version. Answers sort by their exact, case-sensitive keys;
text is never stripped or Unicode/newline-normalized. Checksums alone lowercase.
Changing this serialization contract requires a new schema version.

Construction checks syntax only: callers must establish the canonical application
identity, official destination, reviewed answers, and actual CV bytes separately.
No artifacts, approvals, or delivery state are read or written here.
"""

import hashlib
import json
from collections.abc import Mapping
from email.errors import HeaderParseError
from email.headerregistry import Address
from typing import Annotated, Any, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    HttpUrl,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)

from backend_scout.config import TrackerName

__all__ = [
    "SCHEMA_VERSION",
    "ApplicationPackage",
    "DeliveryRoute",
    "EmailContent",
    "ReviewedAnswer",
]

SCHEMA_VERSION: Final = 1
DeliveryRoute = Literal["portal", "email", "whatsapp"]
_HTTP_URL = TypeAdapter(HttpUrl)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("must be valid UTF-8 text") from exc
    return value


_Text = Annotated[StrictStr, AfterValidator(_nonblank)]


class _ImmutableContent(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Revalidate updates, unlike BaseModel's unvalidated copy operation.

        All fields are immutable; both deep modes produce independent validated
        content. Deliberate Pydantic bypasses such as model_construct are not a
        supported way to create review content or a security boundary.
        """
        return type(self).model_validate(self.model_dump() | dict(update or {}))


class ReviewedAnswer(_ImmutableContent):
    """One reviewed text answer. Keys and values retain their exact spelling."""

    key: _Text
    value: _Text


class EmailContent(_ImmutableContent):
    """One bare mailbox (no display name, comments or domain literal) and text."""

    recipient: _Text
    subject: _Text
    body: _Text

    @field_validator("recipient")
    @classmethod
    def require_single_address(cls, value: str) -> str:
        if any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "[]" for char in value
        ):
            raise ValueError("recipient must be one bare email address")
        try:
            address = Address(addr_spec=value)
        except (ValueError, HeaderParseError) as exc:
            raise ValueError("recipient must be one bare email address") from exc
        if not address.username or not address.domain:
            raise ValueError("recipient must include a local part and domain")
        if address.addr_spec != value:
            raise ValueError("recipient must be one bare email address without comments")
        return value

    @field_validator("subject")
    @classmethod
    def require_single_line_subject(cls, value: str) -> str:
        if any(char in value for char in "\r\n\x00"):
            raise ValueError("subject must not contain header control characters")
        return value


class ApplicationPackage(_ImmutableContent):
    """Exact version-1 review content; no approval or mutable workflow metadata.

    ``cv_version`` identifies the intended CV artifact version and ``cv_sha256``
    hashes that one deliverable's bytes, not a mutable path or a whole directory.
    ``official_destination`` is an HTTPS URL even for email (e.g. the official
    job page); the email recipient is bound separately. WhatsApp destinations
    likewise remain HTTPS URLs. Empty reviewed-answer sequences are valid.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    application_id: _Text
    tracker: TrackerName
    company: _Text
    role: _Text
    official_destination: _Text
    delivery_route: DeliveryRoute
    cv_version: _Text
    cv_sha256: StrictStr
    answers: tuple[ReviewedAnswer, ...] = ()
    email: EmailContent | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("cv_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
            raise ValueError("cv_sha256 must contain exactly 64 hexadecimal characters")
        return value.lower()

    @field_validator("official_destination")
    @classmethod
    def require_https_destination(cls, value: str) -> str:
        if "\\" in value or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError("official_destination must be an HTTPS URL without whitespace")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
            raise ValueError("official_destination must be an absolute HTTPS URL with a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("official_destination must not contain credentials")
        _HTTP_URL.validate_python(value)
        return value

    @field_validator("answers")
    @classmethod
    def require_unique_sorted_answers(
        cls, values: tuple[ReviewedAnswer, ...]
    ) -> tuple[ReviewedAnswer, ...]:
        keys = [answer.key for answer in values]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate answer keys are not allowed")
        return tuple(sorted(values, key=lambda answer: answer.key))

    @model_validator(mode="after")
    def require_route_content(self) -> Self:
        if self.delivery_route == "email" and self.email is None:
            raise ValueError("email delivery requires recipient, subject and body")
        if self.delivery_route != "email" and self.email is not None:
            raise ValueError("email content is only allowed for email delivery")
        return self

    def canonical_json(self) -> str:
        """Return the full, deterministic fingerprint preimage (contains review text)."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def fingerprint(self) -> str:
        """Return a lowercase SHA256 hex digest, never submission authorization."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
