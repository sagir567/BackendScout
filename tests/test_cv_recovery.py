from pathlib import Path

import pytest

from backend_scout.cv_artifacts import (
    CvDraftManifest,
    draft_directory,
    load_manifest,
    verify_manifest,
    write_manifest,
)
from backend_scout.cv_recovery import CvRecoveryConflict, recover_legacy_cv


@pytest.fixture
def legacy(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "legacy"
    directory = root / "explicit-job"
    directory.mkdir(parents=True)
    docx, pdf = directory / "Candidate CV.docx", directory / "Candidate CV.pdf"
    docx.write_bytes(b"synthetic docx")
    pdf.write_bytes(b"synthetic pdf")
    write_manifest(
        directory, "page-123", "Example", "Backend Engineer", docx, pdf,
        tracker="production", tailoring_note="Verified synthetic evidence",
    )
    return root, directory / "manifest.json", tmp_path / "archive"


def recover(legacy: tuple[Path, Path, Path], **changes: str) -> CvDraftManifest:
    root, manifest, archive = legacy
    binding = {
        "notion_page_id": "page-123", "company": "Example",
        "title": "Backend Engineer", "tracker": "production",
    }
    binding.update(changes)
    return recover_legacy_cv(manifest, archive, permitted_source_root=root, **binding)


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def change_manifest(path: Path, **updates: object) -> None:
    manifest = CvDraftManifest.model_validate_json(path.read_text())
    path.write_text(manifest.model_copy(update=updates).model_dump_json())


def test_recovery_copies_and_rebinds_without_changing_source(legacy) -> None:
    root, source_path, archive = legacy
    before = snapshot(root)
    original = CvDraftManifest.model_validate_json(source_path.read_text())
    result = recover(legacy)

    assert snapshot(root) == before
    directory = draft_directory(archive, "Example", "page-123") / "v1"
    assert Path(result.docx_path) == directory / "Candidate CV.docx"
    assert Path(result.pdf_path) == directory / "Candidate CV.pdf"
    assert result.docx_sha256 == original.docx_sha256
    assert result.pdf_sha256 == original.pdf_sha256
    assert result.tailoring_note_sha256 == original.tailoring_note_sha256
    assert result.created_at >= original.created_at
    assert result.tracker == "production"
    assert load_manifest(archive, "Example", "page-123") == result
    verify_manifest(result, result.draft_id)


@pytest.mark.parametrize("field", ["notion_page_id", "company", "title", "tracker"])
def test_wrong_exact_binding_is_rejected(legacy, field) -> None:
    with pytest.raises(ValueError, match="exact job/tracker"):
        recover(legacy, **{field: "wrong"})
    assert not legacy[2].exists()


@pytest.mark.parametrize("suffix", ["docx", "pdf"])
def test_both_checksums_are_verified(legacy, suffix) -> None:
    (legacy[1].parent / f"Candidate CV.{suffix}").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        recover(legacy)
    assert not legacy[2].exists()


def test_missing_pdf_checksum_is_rejected(legacy) -> None:
    change_manifest(legacy[1], pdf_sha256=None)
    with pytest.raises(ValueError, match="Both SHA-256"):
        recover(legacy)
    assert not legacy[2].exists()


@pytest.mark.parametrize("kind", ["manifest", "docx", "pdf"])
def test_source_escape_is_rejected(legacy, tmp_path, kind) -> None:
    root, manifest, archive = legacy
    outside = tmp_path / f"outside.{kind}"
    if kind == "manifest":
        outside.write_bytes(manifest.read_bytes())
        legacy = root, outside, archive
    else:
        original = manifest.parent / f"Candidate CV.{kind}"
        outside.write_bytes(original.read_bytes())
        change_manifest(manifest, **{f"{kind}_path": str(outside)})
    with pytest.raises(ValueError, match="escapes"):
        recover(legacy)
    assert not archive.exists()


@pytest.mark.parametrize("kind", ["manifest", "docx", "pdf", "directory", "root"])
def test_source_symlinks_are_rejected(legacy, kind) -> None:
    root, manifest, archive = legacy
    if kind in {"directory", "root"}:
        target = root if kind == "root" else manifest.parent
        link = root.parent / "link"
        link.symlink_to(target, target_is_directory=True)
        legacy = (link, link / "explicit-job" / "manifest.json", archive) if kind == "root" else (
            root.parent, link / "manifest.json", archive
        )
    else:
        path = manifest if kind == "manifest" else manifest.parent / f"Candidate CV.{kind}"
        saved = path.with_name(f"saved-{path.name}")
        path.rename(saved)
        path.symlink_to(saved)
    with pytest.raises(ValueError, match="Symlinks"):
        recover(legacy)
    assert not archive.exists()


def test_parent_traversal_is_rejected(legacy) -> None:
    root, manifest, archive = legacy
    with pytest.raises(ValueError, match="traversal"):
        recover((root, manifest.parent / ".." / "explicit-job" / "manifest.json", archive))


def test_existing_identical_latest_is_idempotent(legacy) -> None:
    first = recover(legacy)
    before = snapshot(legacy[2])
    assert recover(legacy) == first
    assert snapshot(legacy[2]) == before


@pytest.mark.parametrize("field", ["docx", "pdf", "notion_page_id", "company", "role", "tracker"])
def test_existing_different_valid_latest_is_never_overwritten(legacy, field) -> None:
    result = recover(legacy)
    directory = Path(result.docx_path).parent
    if field in {"docx", "pdf"}:
        Path(getattr(result, f"{field}_path")).write_bytes(b"different valid canonical")
        write_manifest(
            directory, result.notion_page_id, result.company, result.role,
            Path(result.docx_path), Path(result.pdf_path), tracker=result.tracker,
        )
    else:
        change_manifest(directory / "manifest.json", **{field: "different"})
    before = snapshot(legacy[2])
    with pytest.raises(CvRecoveryConflict, match="different valid latest"):
        recover(legacy)
    assert snapshot(legacy[2]) == before


def test_incomplete_version_is_preserved_and_next_version_used(legacy) -> None:
    first = recover(legacy)
    directory = Path(first.docx_path).parent
    Path(first.pdf_path).write_bytes(b"invalid")
    before = snapshot(directory)
    second = recover(legacy)
    assert Path(second.docx_path).parent.name == "v2"
    assert snapshot(directory) == before


def test_never_returns_older_matching_version_when_latest_differs(legacy) -> None:
    first = recover(legacy)
    directory = Path(first.docx_path).parent.parent / "v2"
    directory.mkdir()
    docx, pdf = directory / "cv.docx", directory / "cv.pdf"
    docx.write_bytes(b"newer")
    pdf.write_bytes(b"newer pdf")
    write_manifest(directory, "page-123", "Example", "Backend Engineer", docx, pdf,
                   tracker="production")
    before = snapshot(legacy[2])
    with pytest.raises(CvRecoveryConflict):
        recover(legacy)
    assert snapshot(legacy[2]) == before


def test_latest_manifest_cannot_alias_an_older_version(legacy) -> None:
    first = recover(legacy)
    first_directory = Path(first.docx_path).parent
    second = first_directory.parent / "v2"
    second.mkdir()
    (second / "manifest.json").write_bytes((first_directory / "manifest.json").read_bytes())
    before = snapshot(legacy[2])
    with pytest.raises(CvRecoveryConflict, match="own version"):
        recover(legacy)
    assert snapshot(legacy[2]) == before


@pytest.mark.parametrize("location", ["archive", "company", "version", "manifest", "artifact"])
def test_destination_symlinks_are_rejected(legacy, location, tmp_path) -> None:
    result = recover(legacy)
    directory = Path(result.docx_path).parent
    path = {
        "archive": legacy[2], "company": directory.parent.parent,
        "version": directory, "manifest": directory / "manifest.json",
        "artifact": Path(result.pdf_path),
    }[location]
    saved = tmp_path / "saved"
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=saved.is_dir())
    with pytest.raises(ValueError, match="Symlinks"):
        recover(legacy)


def test_unversioned_canonical_is_not_overwritten(legacy) -> None:
    first = recover(legacy)
    directory = Path(first.docx_path).parent
    parent = directory.parent
    for path in directory.iterdir():
        path.rename(parent / path.name)
    directory.rmdir()
    write_manifest(parent, "page-123", "Example", "Backend Engineer",
                   parent / Path(first.docx_path).name, parent / Path(first.pdf_path).name,
                   tracker="production")
    before = snapshot(legacy[2])
    assert Path(recover(legacy).docx_path).parent == parent
    assert snapshot(legacy[2]) == before


def test_changed_copy_never_publishes_a_manifest(legacy, monkeypatch) -> None:
    import backend_scout.cv_recovery as recovery

    def corrupt_copy(reader, writer):
        writer.write(b"changed during copy")

    before = snapshot(legacy[0])
    monkeypatch.setattr(recovery.shutil, "copyfileobj", corrupt_copy)
    with pytest.raises(ValueError, match="changed"):
        recover(legacy)
    assert snapshot(legacy[0]) == before
    assert not list(legacy[2].rglob("manifest.json"))


def test_reservation_collision_never_overwrites(legacy, monkeypatch) -> None:
    import backend_scout.cv_recovery as recovery

    directory = draft_directory(legacy[2], "Example", "page-123") / "v1"

    def reserve_elsewhere(*args):
        directory.mkdir(parents=True)
        (directory / "sentinel").write_bytes(b"do not replace")
        return directory

    monkeypatch.setattr(recovery, "next_draft_directory", reserve_elsewhere)
    with pytest.raises(CvRecoveryConflict, match="already exists"):
        recover(legacy)
    assert snapshot(directory) == {"sentinel": b"do not replace"}
