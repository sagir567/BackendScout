"""Evidence-first CV drafting shared by interface and workflow adapters."""

import re
import shutil
from pathlib import Path

from backend_scout.career_evidence import DEFAULT_CAREER_EVIDENCE_PATH, load_career_evidence
from backend_scout.config import Settings, TrackerName
from backend_scout.cv_artifacts import (
    CvDraftManifest,
    cv_artifact_paths,
    draft_directory,
    next_draft_directory,
    write_manifest,
)
from backend_scout.cv_coverage import (
    CV_EVIDENCE_COVERAGE_TARGET,
    CvEvidenceGap,
    assess_cv_evidence_coverage,
)
from backend_scout.cv_documents import render_checked_cv_artifacts
from backend_scout.cv_style import DEFAULT_CV_STYLE_PATH, load_cv_style
from backend_scout.cv_tailoring import generate_tailored_cv, validate_tailored_cv_against_evidence
from backend_scout.models import Job
from backend_scout.tailoring_notes import TAILORING_NOTE_ROOT, load_tailoring_note


def draft_job_cv(
    job: Job,
    application_id: str,
    tracker: TrackerName,
    settings: Settings,
    feedback: str | None = None,
    *,
    evidence_path: Path = DEFAULT_CAREER_EVIDENCE_PATH,
    style_path: Path = DEFAULT_CV_STYLE_PATH,
    tailoring_note_root: Path = TAILORING_NOTE_ROOT,
) -> CvDraftManifest:
    """Draft a new immutable CV version, without tracker or notification calls.

    The caller owns lifecycle/authorization checks and supplies revision feedback
    explicitly. Global guidance comes from the style; job notes are tracker-scoped.
    """
    tracker = TrackerName(tracker)
    if (
        not application_id.strip()
        or application_id in {".", ".."}
        or any(separator in application_id for separator in ("/", "\\", "\x00"))
    ):
        raise ValueError("Application ID must be a nonblank path component")
    _require_full_job_description(job)
    evidence = load_career_evidence(evidence_path)
    style = load_cv_style(style_path)
    coverage = assess_cv_evidence_coverage(evidence, job)
    if coverage.coverage_score < CV_EVIDENCE_COVERAGE_TARGET:
        raise CvEvidenceGap(coverage)
    if not settings.openai_api_key:
        raise ValueError("Missing OPENAI_API_KEY for CV drafting")
    if settings.cv_archive_root.resolve() == Path("applications").resolve():
        raise ValueError("Set CV_ARCHIVE_ROOT to a private folder outside the repository.")

    root = draft_directory(settings.cv_archive_root, job.company, application_id)
    if not root.resolve().is_relative_to(settings.cv_archive_root.resolve()):
        raise ValueError("CV archive application directory escapes the configured private root")
    _require_archive_binding(root, job, application_id, tracker)
    note = load_tailoring_note(application_id, tracker.value, tailoring_note_root)
    generation_options = {}
    if settings.portal_delivery_mode == "hybrid":
        from backend_scout.budget_config import configured_budget

        generation_options["usage_budget"] = configured_budget(settings)
    draft = generate_tailored_cv(
        evidence,
        job,
        style,
        settings.openai_api_key,
        settings.openai_model_high_quality,
        feedback,
        note.text if note else None,
        **generation_options,
    )
    validate_tailored_cv_against_evidence(draft, evidence, style, job)

    directory = next_draft_directory(settings.cv_archive_root, job.company, application_id)
    docx_path, pdf_path = cv_artifact_paths(directory, evidence.identity.full_name)
    # Reserve exclusively: a competing draft must never overwrite another version.
    directory.mkdir(parents=True, exist_ok=False)
    try:
        _require_archive_binding(root, job, application_id, tracker, reserved_directory=directory)
        render_checked_cv_artifacts(evidence, draft, docx_path, pdf_path, style)
        return write_manifest(
            directory,
            application_id,
            job.company,
            job.title,
            docx_path,
            pdf_path,
            tracker.value,
            note.text if note else None,
        )
    except Exception:
        # Failed new versions must not hide the last complete, reviewable manifest.
        shutil.rmtree(directory)
        raise


def _require_full_job_description(job: Job) -> None:
    def normalized(value: str) -> str:
        return " ".join(re.findall(r"\w+", value.casefold()))

    description = normalized(job.description)
    placeholders = {
        normalized(job.title),
        normalized(f"{job.title} at {job.company}"),
        normalized(f"{job.title} {job.company}"),
        normalized(f"{job.company} {job.title}"),
        "published public ats job posting",
        "published lin srael job listing",
        "indeed email recommendation",
        "no description available",
        "job description unavailable",
    }
    if not description or description in placeholders:
        raise ValueError(
            "Missing full job requirements: the supplied job description is title-only or a "
            "placeholder. Provide the full job description before CV evidence coverage and drafting."
        )


def _require_archive_binding(
    root: Path,
    job: Job,
    application_id: str,
    tracker: TrackerName,
    *,
    reserved_directory: Path | None = None,
) -> None:
    paths = [root / "manifest.json"]
    paths.extend(
        path / "manifest.json"
        for path in root.glob("v*")
        if path.is_dir() and path.name.removeprefix("v").isdigit()
    )
    for path in paths:
        if not path.is_file():
            if reserved_directory is not None and path.parent not in {root, reserved_directory}:
                raise ValueError("Another CV version is incomplete; retry after drafting finishes")
            continue
        manifest = CvDraftManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            manifest.notion_page_id != application_id
            or manifest.tracker != tracker.value
            or manifest.company != job.company
        ):
            raise ValueError("Existing CV manifest does not match this application and tracker")
