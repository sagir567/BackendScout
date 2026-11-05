import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend_scout import cv_service
from backend_scout.config import Settings, TrackerName
from backend_scout.cv_artifacts import load_manifest, verify_manifest, write_manifest
from backend_scout.cv_documents import PdfLayoutMetrics
from backend_scout.cv_tailoring import build_evidence_only_draft
from backend_scout.models import CareerEvidence, CvStyle, Job, TailoredCv
from backend_scout.tailoring_notes import save_tailoring_note


@pytest.fixture
def drafting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    evidence = CareerEvidence.model_validate(
        {
            "identity": {"full_name": "Synthetic Candidate", "email": "candidate@example.test"},
            "skills": [{"category": "Backend", "items": ["Python"]}],
            "projects": [
                {
                    "id": "project-python",
                    "name": "Synthetic API",
                    "selection_keywords": ["Python"],
                    "bullets": [
                        {"id": "python-api", "text": "Built a Python API as a personal project."}
                    ],
                }
            ],
        }
    )
    style = CvStyle(
        writing_rules=["Keep personal projects distinct from employment."],
        tailoring_guidance=["Prefer the verified Python project."],
    )
    evidence_path, style_path = tmp_path / "evidence.yaml", tmp_path / "style.yaml"
    # JSON is valid YAML and keeps these entirely synthetic fixtures easy to audit.
    evidence_path.write_text(evidence.model_dump_json(), encoding="utf-8")
    style_path.write_text(style.model_dump_json(), encoding="utf-8")
    job = Job(
        source="fixture",
        source_url="https://employer.example.test/jobs/backend",
        company="Synthetic Employer",
        title="Backend Developer",
        description="Build and maintain Python APIs. Requirements: Python programming.",
        required_skills=["Python"],
    )
    settings = Settings.model_construct(
        openai_api_key="synthetic-api-key",
        openai_model_high_quality="configured-test-model",
        cv_archive_root=tmp_path / "archive",
    )
    generated = build_evidence_only_draft(evidence, job)
    generate = Mock(return_value=generated)

    def fake_render(evidence, draft, docx_path, pdf_path, style):
        docx_path.write_bytes(f"synthetic docx {docx_path.parent.name}".encode())
        pdf_path.write_bytes(f"synthetic pdf {pdf_path.parent.name}".encode())
        return PdfLayoutMetrics(page_count=1, content_fill_ratio=0.95)

    render = Mock(side_effect=fake_render)
    monkeypatch.setattr(cv_service, "generate_tailored_cv", generate)
    monkeypatch.setattr(cv_service, "render_checked_cv_artifacts", render)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(
        evidence=evidence,
        style=style,
        job=job,
        settings=settings,
        draft=generated,
        generate=generate,
        render=render,
        paths={
            "evidence_path": evidence_path,
            "style_path": style_path,
            "tailoring_note_root": tmp_path / "notes",
        },
    )


def _draft(drafting: SimpleNamespace, **kwargs):
    arguments = {
        "job": drafting.job,
        "application_id": "application-123",
        "tracker": TrackerName.TEST,
        "settings": drafting.settings,
        **drafting.paths,
    }
    return cv_service.draft_job_cv(**(arguments | kwargs))


def test_draft_from_passed_job_without_notion_or_telegram(drafting: SimpleNamespace) -> None:
    manifest = _draft(drafting, tracker=TrackerName.PRODUCTION)

    assert drafting.settings.notion_api_key is None
    assert drafting.settings.telegram_bot_token is None
    assert manifest.notion_page_id == "application-123"
    assert manifest.tracker == "production"
    assert manifest.company == drafting.job.company
    assert manifest.role == drafting.job.title
    assert Path(manifest.docx_path).name == "Synthetic Candidate CV.docx"
    assert Path(manifest.pdf_path).name == "Synthetic Candidate CV.pdf"
    assert Path(manifest.pdf_path).parent == (
        drafting.settings.cv_archive_root / "Synthetic-Employer" / "application-123" / "v1"
    )
    verify_manifest(manifest, manifest.draft_id)
    assert (
        load_manifest(drafting.settings.cv_archive_root, drafting.job.company, "application-123")
        == manifest
    )
    drafting.generate.assert_called_once_with(
        drafting.evidence,
        drafting.job,
        drafting.style,
        "synthetic-api-key",
        "configured-test-model",
        None,
        None,
    )
    assert drafting.generate.call_args.args[1] is drafting.job
    drafting.render.assert_called_once_with(
        drafting.evidence,
        drafting.draft,
        Path(manifest.docx_path),
        Path(manifest.pdf_path),
        drafting.style,
    )


def test_global_guidance_job_note_and_feedback_reach_generator(drafting: SimpleNamespace) -> None:
    save_tailoring_note(
        "application-123",
        "test",
        "Emphasize the personal API project.",
        drafting.paths["tailoring_note_root"],
    )
    save_tailoring_note(
        "application-123",
        "production",
        "Do not use another tracker's note.",
        drafting.paths["tailoring_note_root"],
    )
    feedback = "Shorten the project bullet without changing its ownership."

    manifest = _draft(drafting, feedback=feedback)

    args = drafting.generate.call_args.args
    assert args[2].tailoring_guidance == ["Prefer the verified Python project."]
    assert args[2].writing_rules == ["Keep personal projects distinct from employment."]
    assert args[5:] == (feedback, "Emphasize the personal API project.")
    assert (
        manifest.tailoring_note_sha256
        == hashlib.sha256(b"Emphasize the personal API project.").hexdigest()
    )


def test_revision_preserves_prior_artifacts_and_manifest(drafting: SimpleNamespace) -> None:
    first = _draft(drafting)
    directory = Path(first.docx_path).parent
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    revised = _draft(drafting, feedback="Use a shorter bullet.")

    assert Path(revised.docx_path).parent.name == "v2"
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    verify_manifest(first, first.draft_id)
    assert first.draft_id != revised.draft_id
    assert (
        load_manifest(drafting.settings.cv_archive_root, drafting.job.company, "application-123")
        == revised
    )


@pytest.mark.parametrize(
    "description",
    [
        "Backend Developer",
        " BACKEND   DEVELOPER. ",
        "Backend Developer at Synthetic Employer",
        "Backend Developer - Synthetic Employer",
        "Synthetic Employer: Backend Developer",
        "Published public ATS job posting.",
        "Indeed email recommendation.",
        "No description available",
        "Job description unavailable",
        "...",
    ],
)
@pytest.mark.parametrize("skills", [[], ["Python"]])
def test_placeholder_requires_full_requirements_before_coverage_or_generation(
    drafting: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, description: str, skills: list[str]
) -> None:
    coverage = Mock(side_effect=AssertionError("Placeholder must not receive coverage"))
    monkeypatch.setattr(cv_service, "assess_cv_evidence_coverage", coverage)
    job = drafting.job.model_copy(update={"description": description, "required_skills": skills})

    with pytest.raises(ValueError, match="Missing full job requirements"):
        _draft(drafting, job=job)

    coverage.assert_not_called()
    drafting.generate.assert_not_called()
    drafting.render.assert_not_called()
    assert not drafting.settings.cv_archive_root.exists()


def test_full_description_without_extracted_skills_keeps_existing_coverage_policy(
    drafting: SimpleNamespace,
) -> None:
    _draft(drafting, job=drafting.job.model_copy(update={"required_skills": []}))

    drafting.generate.assert_called_once()


def test_missing_factual_evidence_blocks_generation_without_changing_sources(
    drafting: SimpleNamespace,
) -> None:
    before = drafting.paths["evidence_path"].read_bytes()
    job = drafting.job.model_copy(update={"required_skills": ["Python", "Kubernetes"]})

    with pytest.raises(ValueError, match="factual evidence coverage.*90/100"):
        _draft(drafting, job=job)

    assert drafting.paths["evidence_path"].read_bytes() == before
    drafting.generate.assert_not_called()
    drafting.render.assert_not_called()
    assert not drafting.settings.cv_archive_root.exists()


@pytest.mark.parametrize(
    "invalid_draft",
    [
        TailoredCv(summary="Unsupported claim", summary_evidence_ids=["invented"]),
        TailoredCv.model_validate({"skills": [{"category": "Cloud", "items": ["Kubernetes"]}]}),
        TailoredCv(headline="Forbidden headline"),
    ],
)
def test_generated_draft_is_validated_before_rendering(
    drafting: SimpleNamespace, invalid_draft: TailoredCv
) -> None:
    drafting.generate.return_value = invalid_draft

    with pytest.raises(ValueError):
        _draft(drafting)

    drafting.render.assert_not_called()
    assert not drafting.settings.cv_archive_root.exists()


@pytest.mark.parametrize(
    "failure", [ValueError("one-page CV page fill failed"), RuntimeError("PDF failed")]
)
def test_failed_render_preserves_last_reviewable_version(
    drafting: SimpleNamespace, failure: Exception
) -> None:
    first = _draft(drafting)
    root = Path(first.docx_path).parent.parent
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def fail_after_partial_render(evidence, draft, docx_path, pdf_path, style):
        docx_path.write_bytes(b"partial new synthetic docx")
        raise failure

    drafting.render.side_effect = fail_after_partial_render

    with pytest.raises(type(failure), match=str(failure)):
        _draft(drafting, feedback="Revise the draft.")

    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert not (root / "v2").exists()
    assert (
        load_manifest(drafting.settings.cv_archive_root, drafting.job.company, "application-123")
        == first
    )


def test_failed_manifest_removes_only_the_new_version(
    drafting: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _draft(drafting)
    monkeypatch.setattr(cv_service, "write_manifest", Mock(side_effect=OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        _draft(drafting)

    assert not (Path(first.docx_path).parent.parent / "v2").exists()
    verify_manifest(first, first.draft_id)


@pytest.mark.parametrize("legacy", [False, True])
def test_other_tracker_cannot_rebind_existing_archive(
    drafting: SimpleNamespace, legacy: bool
) -> None:
    if legacy:
        root = drafting.settings.cv_archive_root / "Synthetic-Employer" / "application-123"
        root.mkdir(parents=True)
        docx, pdf = root / "old.docx", root / "old.pdf"
        docx.write_bytes(b"old synthetic docx")
        pdf.write_bytes(b"old synthetic pdf")
        first = write_manifest(
            root, "application-123", drafting.job.company, drafting.job.title, docx, pdf
        )
    else:
        first = _draft(drafting)
        root = Path(first.docx_path).parent.parent
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    drafting.generate.reset_mock()

    with pytest.raises(ValueError, match="application and tracker"):
        _draft(drafting, tracker=TrackerName.PRODUCTION)

    drafting.generate.assert_not_called()
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    verify_manifest(first, first.draft_id)


def test_existing_manifest_application_identity_is_checked(drafting: SimpleNamespace) -> None:
    first = _draft(drafting)
    path = Path(first.docx_path).parent / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["notion_page_id"] = "another-application"
    path.write_text(json.dumps(payload), encoding="utf-8")
    drafting.generate.reset_mock()

    with pytest.raises(ValueError, match="application and tracker"):
        _draft(drafting)

    drafting.generate.assert_not_called()


def test_archive_binding_is_rechecked_after_generation(drafting: SimpleNamespace) -> None:
    def generate_while_another_tracker_finishes(*args):
        root = drafting.settings.cv_archive_root / "Synthetic-Employer" / "application-123" / "v1"
        root.mkdir(parents=True)
        docx, pdf = root / "other.docx", root / "other.pdf"
        docx.write_bytes(b"synthetic other tracker's docx")
        pdf.write_bytes(b"synthetic other tracker's pdf")
        write_manifest(
            root,
            "application-123",
            drafting.job.company,
            drafting.job.title,
            docx,
            pdf,
            "production",
        )
        return drafting.draft

    drafting.generate.side_effect = generate_while_another_tracker_finishes

    with pytest.raises(ValueError, match="application and tracker"):
        _draft(drafting)

    drafting.render.assert_not_called()
    root = drafting.settings.cv_archive_root / "Synthetic-Employer" / "application-123"
    assert not (root / "v2").exists()


def test_existing_directory_is_never_overwritten(
    drafting: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _draft(drafting)
    directory = Path(first.docx_path).parent
    before = {path: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setattr(cv_service, "next_draft_directory", Mock(return_value=directory))
    drafting.render.reset_mock()

    with pytest.raises(FileExistsError):
        _draft(drafting)

    drafting.render.assert_not_called()
    assert before == {path: path.read_bytes() for path in directory.iterdir()}


def test_concurrent_incomplete_version_cannot_acquire_another_tracker_binding(
    drafting: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = drafting.settings.cv_archive_root / "Synthetic-Employer" / "application-123"

    def another_draft_reserves_first(*args):
        pending = root / "v1"
        pending.mkdir(parents=True)
        (pending / "pending.docx").write_bytes(b"other draft in progress")
        return root / "v2"

    monkeypatch.setattr(cv_service, "next_draft_directory", another_draft_reserves_first)

    with pytest.raises(ValueError, match="Another CV version is incomplete"):
        _draft(drafting, tracker=TrackerName.PRODUCTION)

    assert (root / "v1" / "pending.docx").read_bytes() == b"other draft in progress"
    assert not (root / "v2").exists()
    drafting.render.assert_not_called()


def test_note_binding_is_checked_before_generation(drafting: SimpleNamespace) -> None:
    path = drafting.paths["tailoring_note_root"] / "test" / "application-123.json"
    note = save_tailoring_note(
        "application-123",
        "test",
        "Focus on the verified project.",
        drafting.paths["tailoring_note_root"],
    )
    path.write_text(
        note.model_copy(update={"tracker": "production"}).model_dump_json(), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Tailoring note does not match"):
        _draft(drafting)

    drafting.generate.assert_not_called()


def test_other_trackers_note_is_not_used_as_fallback(drafting: SimpleNamespace) -> None:
    save_tailoring_note(
        "application-123",
        "production",
        "Only for production.",
        drafting.paths["tailoring_note_root"],
    )

    manifest = _draft(drafting)

    assert drafting.generate.call_args.args[-1] is None
    assert manifest.tailoring_note_sha256 is None


def test_generation_failure_leaves_previous_version_unchanged(drafting: SimpleNamespace) -> None:
    first = _draft(drafting)
    drafting.generate.side_effect = RuntimeError("synthetic generation failure")
    drafting.render.reset_mock()

    with pytest.raises(RuntimeError, match="synthetic generation failure"):
        _draft(drafting, feedback="Revise the project bullet.")

    verify_manifest(first, first.draft_id)
    assert not (Path(first.docx_path).parent.parent / "v2").exists()
    drafting.render.assert_not_called()


def test_default_private_config_paths_are_supported(drafting: SimpleNamespace) -> None:
    cv_service.DEFAULT_CAREER_EVIDENCE_PATH.parent.mkdir(parents=True)
    cv_service.DEFAULT_CAREER_EVIDENCE_PATH.write_text(
        drafting.evidence.model_dump_json(), encoding="utf-8"
    )
    cv_service.DEFAULT_CV_STYLE_PATH.write_text(drafting.style.model_dump_json(), encoding="utf-8")
    save_tailoring_note("application-123", "test", "Default-path synthetic guidance.")

    manifest = cv_service.draft_job_cv(
        drafting.job, "application-123", TrackerName.TEST, drafting.settings
    )

    assert drafting.generate.call_args.args[2] == drafting.style
    assert drafting.generate.call_args.args[-1] == "Default-path synthetic guidance."
    verify_manifest(manifest, manifest.draft_id)


@pytest.mark.parametrize("application_id", ["", " ", ".", "..", "../escape", "a/b", "a\\b"])
def test_unsafe_application_id_is_rejected(drafting: SimpleNamespace, application_id: str) -> None:
    with pytest.raises(ValueError, match="Application ID"):
        _draft(drafting, application_id=application_id)

    drafting.generate.assert_not_called()


def test_archive_cannot_escape_through_an_existing_symlink(
    drafting: SimpleNamespace, tmp_path: Path
) -> None:
    root = drafting.settings.cv_archive_root
    root.mkdir()
    outside = tmp_path / "outside-archive"
    outside.mkdir()
    (root / "Synthetic-Employer").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the configured private root"):
        _draft(drafting)

    assert not list(outside.iterdir())
    drafting.generate.assert_not_called()


def test_missing_model_key_is_rejected(drafting: SimpleNamespace) -> None:
    drafting.settings.openai_api_key = None

    with pytest.raises(ValueError, match="Missing OPENAI_API_KEY"):
        _draft(drafting)

    drafting.generate.assert_not_called()


def test_default_repository_archive_is_rejected(drafting: SimpleNamespace) -> None:
    drafting.settings.cv_archive_root = Path("applications")

    with pytest.raises(ValueError, match="private folder outside the repository"):
        _draft(drafting)

    drafting.generate.assert_not_called()
