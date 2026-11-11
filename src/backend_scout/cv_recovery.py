"""Explicit, filesystem-only recovery of verified legacy CV pairs."""

import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path

from backend_scout.cv_artifacts import (
    CvDraftManifest,
    draft_directory,
    next_draft_directory,
    verify_manifest,
)


class CvRecoveryConflict(ValueError):
    """A canonical artifact cannot safely be reused or replaced."""


def _safe_path(path: Path, root: Path | None = None) -> Path:
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Parent traversal is not permitted")
    path = path.absolute()
    if root is not None and not path.is_relative_to(root):
        raise ValueError("Path escapes the permitted root")
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("Symlinks are not permitted")
    return path


def _read_manifest(path: Path) -> CvDraftManifest:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Manifest must be a regular file")
    return CvDraftManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _verify_pair(manifest: CvDraftManifest, root: Path) -> tuple[Path, Path]:
    paths = []
    for value, suffix in ((manifest.docx_path, ".docx"), (manifest.pdf_path, ".pdf")):
        if not Path(value).is_absolute():
            raise ValueError("Manifest artifact paths must be absolute")
        path = _safe_path(Path(value), root)
        if path.suffix.lower() != suffix or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("CV artifacts must be regular DOCX/PDF files")
        paths.append(path)
    for digest in (manifest.docx_sha256, manifest.pdf_sha256):
        if digest is None or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Both SHA-256 checksums are required")
    verify_manifest(manifest, manifest.draft_id)
    return paths[0], paths[1]


def recover_legacy_cv(
    source_manifest_path: Path,
    destination_archive_root: Path,
    *,
    permitted_source_root: Path,
    notion_page_id: str,
    company: str,
    title: str,
    tracker: str,
) -> CvDraftManifest:
    """Copy one exactly bound legacy pair into a new canonical version.

    Source paths are explicit, read-only, regular files beneath the permitted
    source root. Only an identical, exactly bound latest canonical pair may be
    returned without copying. No approval, task, or database state is touched.
    Call with an exclusively owned archive (no concurrent filesystem writers).
    """
    if not all(value.strip() for value in (notion_page_id, company, title, tracker)):
        raise ValueError("Exact nonempty job and tracker binding is required")
    source_root = _safe_path(permitted_source_root)
    if not source_root.is_dir():
        raise ValueError("Permitted source root must exist")
    source_path = _safe_path(source_manifest_path, source_root)
    source = _read_manifest(source_path)
    binding = (notion_page_id, company, title, tracker)
    if (source.notion_page_id, source.company, source.role, source.tracker) != binding:
        raise ValueError("Source manifest does not match the exact job/tracker binding")
    source_docx, source_pdf = _verify_pair(source, source_root)

    archive = _safe_path(destination_archive_root)
    job_root = _safe_path(draft_directory(archive, company, notion_page_id), archive)
    versions = []
    if job_root.exists():
        for entry in job_root.iterdir():
            if entry.name.startswith("v") and entry.name[1:].isdigit():
                _safe_path(entry, archive)
                if not entry.is_dir():
                    raise CvRecoveryConflict("Canonical version is not a directory")
                versions.append(entry)
    latest = max(versions, key=lambda entry: int(entry.name[1:])) if versions else job_root
    latest_path = _safe_path(latest / "manifest.json", archive)
    if latest_path.exists():
        try:
            existing = _read_manifest(latest_path)
        except (ValueError, OSError):
            existing = None
        if existing is not None:
            existing_paths = tuple(
                _safe_path(Path(value), archive)
                for value in (existing.docx_path, existing.pdf_path)
            )
            if any(path.parent != latest for path in existing_paths):
                raise CvRecoveryConflict("Latest manifest points outside its own version")
            try:
                _verify_pair(existing, latest)
            except (ValueError, OSError):
                # An incomplete latest version is preserved, never overwritten.
                existing = None
        if existing is not None:
            if (
                (existing.notion_page_id, existing.company, existing.role, existing.tracker)
                != binding
                or existing.docx_sha256 != source.docx_sha256
                or existing.pdf_sha256 != source.pdf_sha256
            ):
                raise CvRecoveryConflict("A different valid latest canonical CV already exists")
            return existing

    directory = _safe_path(next_draft_directory(archive, company, notion_page_id), archive)
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()  # Reserve a fresh version; never reuse an existing directory.
    except FileExistsError as exc:
        raise CvRecoveryConflict("Canonical version already exists") from exc
    docx, pdf = directory / source_docx.name, directory / source_pdf.name
    for source_file, target in ((source_docx, docx), (source_pdf, pdf)):
        with source_file.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    recovered = CvDraftManifest(
        draft_id=source.docx_sha256[:8],
        notion_page_id=notion_page_id,
        company=company,
        role=title,
        tracker=tracker,
        docx_path=str(docx),
        pdf_path=str(pdf),
        docx_sha256=source.docx_sha256,
        pdf_sha256=source.pdf_sha256,
        tailoring_note_sha256=source.tailoring_note_sha256,
        created_at=datetime.now(UTC),
    )
    _verify_pair(recovered, directory)
    # Publish the manifest only after both copied files pass checksum verification.
    with (directory / "manifest.json").open("x", encoding="utf-8") as writer:
        writer.write(recovered.model_dump_json(indent=2))
    return recovered
