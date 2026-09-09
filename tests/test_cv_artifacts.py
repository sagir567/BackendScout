from pathlib import Path

import pytest

from backend_scout.cv_artifacts import (
    draft_directory,
    load_manifest,
    next_draft_directory,
    verify_manifest,
    write_manifest,
)


def test_manifest_binds_a_draft_to_its_exact_docx(tmp_path: Path) -> None:
    directory = draft_directory(tmp_path, "Example Cloud", "page-123")
    directory.mkdir(parents=True)
    docx_path = directory / "cv_draft.docx"
    pdf_path = directory / "cv_draft.pdf"
    docx_path.write_bytes(b"original docx bytes")
    pdf_path.write_bytes(b"pdf bytes")

    written = write_manifest(directory, "page-123", "Example Cloud", "Backend Engineer", docx_path, pdf_path)
    loaded = load_manifest(tmp_path, "Example Cloud", "page-123")

    verify_manifest(loaded, written.draft_id)
    assert loaded.tracker == "test"
    assert loaded.pdf_sha256 is not None
    docx_path.write_bytes(b"changed docx bytes")
    with pytest.raises(ValueError, match="changed after"):
        verify_manifest(loaded, written.draft_id)


def test_manifest_rejects_a_changed_pdf_when_it_has_a_checksum(tmp_path: Path) -> None:
    directory = draft_directory(tmp_path, "Example Cloud", "page-123")
    directory.mkdir(parents=True)
    docx_path = directory / "cv_draft.docx"
    pdf_path = directory / "cv_draft.pdf"
    docx_path.write_bytes(b"docx")
    pdf_path.write_bytes(b"original pdf")
    manifest = write_manifest(directory, "page-123", "Example Cloud", "Backend Engineer", docx_path, pdf_path)

    pdf_path.write_bytes(b"changed pdf")
    with pytest.raises(ValueError, match="PDF changed"):
        verify_manifest(manifest, manifest.draft_id)


def test_latest_versioned_manifest_rejects_an_older_draft_approval(tmp_path: Path) -> None:
    page_id = "page-123"
    company = "Example Cloud"
    first = next_draft_directory(tmp_path, company, page_id)
    first.mkdir(parents=True)
    first_docx, first_pdf = first / "cv_draft.docx", first / "cv_draft.pdf"
    first_docx.write_bytes(b"first")
    first_pdf.write_bytes(b"first pdf")
    first_manifest = write_manifest(first, page_id, company, "Backend Engineer", first_docx, first_pdf)

    second = next_draft_directory(tmp_path, company, page_id)
    second.mkdir(parents=True)
    second_docx, second_pdf = second / "cv_draft.docx", second / "cv_draft.pdf"
    second_docx.write_bytes(b"second")
    second_pdf.write_bytes(b"second pdf")
    second_manifest = write_manifest(second, page_id, company, "Backend Engineer", second_docx, second_pdf)

    latest = load_manifest(tmp_path, company, page_id)
    assert latest.draft_id == second_manifest.draft_id
    with pytest.raises(ValueError, match="does not match"):
        verify_manifest(latest, first_manifest.draft_id)


def test_manifest_records_the_tracker_and_tailoring_note_digest(tmp_path: Path) -> None:
    directory = draft_directory(tmp_path, "Example", "page-9")
    directory.mkdir(parents=True)
    docx_path, pdf_path = directory / "cv.docx", directory / "cv.pdf"
    docx_path.write_bytes(b"docx")
    pdf_path.write_bytes(b"pdf")

    manifest = write_manifest(
        directory,
        "page-9",
        "Example",
        "Backend Engineer",
        docx_path,
        pdf_path,
        tracker="production",
        tailoring_note="Emphasize the verified .NET 8 project.",
    )

    assert manifest.tracker == "production"
    assert manifest.tailoring_note_sha256 is not None
