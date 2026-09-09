"""Private, one-time approvals for a final portal submit click."""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend_scout.config import TrackerName
from backend_scout.cv_artifacts import CvDraftManifest

PORTAL_AUTHORIZATIONS_ROOT = Path("data/portal_authorizations")
PORTAL_SUBMIT_AUTHORIZATION_TTL = timedelta(minutes=15)


class PortalSubmitAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_id: str
    notion_page_id: str
    tracker: TrackerName
    application_url: str
    draft_id: str
    docx_sha256: str
    pdf_sha256: str
    requested_at: datetime
    expires_at: datetime
    authorized_at: datetime | None = None
    consumed_at: datetime | None = None


def create_portal_submit_authorization(
    page_id: str,
    tracker: TrackerName,
    application_url: str,
    manifest: CvDraftManifest,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> PortalSubmitAuthorization:
    now = datetime.now(UTC)
    authorization = PortalSubmitAuthorization(
        authorization_id=secrets.token_urlsafe(8),
        notion_page_id=page_id,
        tracker=tracker,
        application_url=application_url,
        draft_id=manifest.draft_id,
        docx_sha256=manifest.docx_sha256,
        pdf_sha256=manifest.pdf_sha256 or _sha256_for_path(manifest.pdf_path),
        requested_at=now,
        expires_at=now + PORTAL_SUBMIT_AUTHORIZATION_TTL,
    )
    save_portal_submit_authorization(authorization, root)
    return authorization


def authorize_portal_submit(
    page_id: str,
    authorization_id: str,
    tracker: TrackerName,
    application_url: str,
    manifest: CvDraftManifest,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> PortalSubmitAuthorization:
    authorization = load_portal_submit_authorization(page_id, authorization_id, root)
    _validate_binding(authorization, tracker, application_url, manifest)
    if authorization.authorized_at is not None:
        raise ValueError("Portal submission was already authorized")
    authorization = authorization.model_copy(update={"authorized_at": datetime.now(UTC)})
    save_portal_submit_authorization(authorization, root)
    return authorization


def require_portal_submit_authorization(
    page_id: str,
    authorization_id: str,
    tracker: TrackerName,
    application_url: str,
    manifest: CvDraftManifest,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> PortalSubmitAuthorization:
    authorization = load_portal_submit_authorization(page_id, authorization_id, root)
    _validate_binding(authorization, tracker, application_url, manifest)
    if authorization.authorized_at is None:
        raise ValueError("Portal submission still needs Telegram approval")
    if authorization.consumed_at is not None:
        raise ValueError("Portal submission authorization was already used")
    return authorization


def find_pending_portal_submit_authorization(
    page_id: str,
    tracker: TrackerName,
    application_url: str,
    manifest: CvDraftManifest,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> PortalSubmitAuthorization:
    authorizations: list[PortalSubmitAuthorization] = []
    for path in root.glob(f"{page_id}-*.json") if root.is_dir() else []:
        try:
            authorization = PortalSubmitAuthorization.model_validate_json(path.read_text(encoding="utf-8"))
            _validate_binding(authorization, tracker, application_url, manifest)
        except (OSError, ValueError):
            continue
        if authorization.authorized_at is not None and authorization.consumed_at is None:
            authorizations.append(authorization)
    if not authorizations:
        raise ValueError("No active Telegram portal submission approval was found")
    return max(authorizations, key=lambda authorization: authorization.authorized_at or authorization.requested_at)


def consume_portal_submit_authorization(
    page_id: str,
    authorization_id: str,
    tracker: TrackerName,
    application_url: str,
    manifest: CvDraftManifest,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> PortalSubmitAuthorization:
    authorization = require_portal_submit_authorization(
        page_id, authorization_id, tracker, application_url, manifest, root
    )
    authorization = authorization.model_copy(update={"consumed_at": datetime.now(UTC)})
    save_portal_submit_authorization(authorization, root)
    return authorization


def load_portal_submit_authorization(
    page_id: str,
    authorization_id: str,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> PortalSubmitAuthorization:
    path = _authorization_path(root, page_id, authorization_id)
    try:
        return PortalSubmitAuthorization.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Portal submission authorization was not found") from exc


def save_portal_submit_authorization(
    authorization: PortalSubmitAuthorization,
    root: Path = PORTAL_AUTHORIZATIONS_ROOT,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = _authorization_path(root, authorization.notion_page_id, authorization.authorization_id)
    path.write_text(authorization.model_dump_json(indent=2), encoding="utf-8")
    return path


def _validate_binding(
    authorization: PortalSubmitAuthorization,
    tracker: TrackerName,
    application_url: str,
    manifest: CvDraftManifest,
) -> None:
    if authorization.notion_page_id != manifest.notion_page_id:
        raise ValueError("Portal submission authorization belongs to a different application")
    if authorization.tracker != tracker or manifest.tracker != tracker.value:
        raise ValueError("Portal submission authorization belongs to a different tracker")
    if authorization.application_url != application_url:
        raise ValueError("Portal submission authorization belongs to a different portal URL")
    if authorization.draft_id != manifest.draft_id:
        raise ValueError("Portal submission authorization belongs to an older CV draft")
    pdf_sha256 = manifest.pdf_sha256 or _sha256_for_path(manifest.pdf_path)
    if authorization.docx_sha256 != manifest.docx_sha256 or authorization.pdf_sha256 != pdf_sha256:
        raise ValueError("Portal submission authorization does not match the approved CV files")
    if datetime.now(UTC) >= authorization.expires_at:
        raise ValueError("Portal submission authorization expired; request a new Telegram approval")


def _authorization_path(root: Path, page_id: str, authorization_id: str) -> Path:
    return root / f"{page_id}-{authorization_id}.json"


def _sha256_for_path(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
