import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class CvDraftManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    notion_page_id: str
    company: str
    role: str
    docx_path: str
    pdf_path: str
    docx_sha256: str
    created_at: datetime


def draft_directory(archive_root: Path, company: str, notion_page_id: str) -> Path:
    return archive_root / _safe_path_component(company) / _safe_path_component(notion_page_id)


def next_draft_directory(archive_root: Path, company: str, notion_page_id: str) -> Path:
    """Return the next immutable version directory for one application."""
    root = draft_directory(archive_root, company, notion_page_id)
    versions = [
        int(path.name.removeprefix("v"))
        for path in root.glob("v*")
        if path.is_dir() and path.name.removeprefix("v").isdigit()
    ]
    return root / f"v{max(versions, default=0) + 1}"


def write_manifest(
    directory: Path,
    notion_page_id: str,
    company: str,
    role: str,
    docx_path: Path,
    pdf_path: Path,
) -> CvDraftManifest:
    digest = sha256_file(docx_path)
    manifest = CvDraftManifest(
        draft_id=digest[:8],
        notion_page_id=notion_page_id,
        company=company,
        role=role,
        docx_path=str(docx_path.resolve()),
        pdf_path=str(pdf_path.resolve()),
        docx_sha256=digest,
        created_at=datetime.now(UTC),
    )
    (directory / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest


def load_manifest(archive_root: Path, company: str, notion_page_id: str) -> CvDraftManifest:
    root = draft_directory(archive_root, company, notion_page_id)
    versions = sorted(
        (path for path in root.glob("v*") if path.is_dir() and path.name.removeprefix("v").isdigit()),
        key=lambda path: int(path.name.removeprefix("v")),
    )
    path = (versions[-1] if versions else root) / "manifest.json"
    return CvDraftManifest.model_validate_json(path.read_text(encoding="utf-8"))


def verify_manifest(manifest: CvDraftManifest, expected_draft_id: str) -> None:
    if manifest.draft_id != expected_draft_id:
        raise ValueError("Telegram approval does not match the current CV draft")
    docx_path = Path(manifest.docx_path)
    pdf_path = Path(manifest.pdf_path)
    if not docx_path.is_file() or not pdf_path.is_file():
        raise ValueError("CV draft artifact is missing")
    if sha256_file(docx_path) != manifest.docx_sha256:
        raise ValueError("CV draft changed after it was sent for approval")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return normalized[:80] or "untitled"
