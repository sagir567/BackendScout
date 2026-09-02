from pathlib import Path

from backend_scout.cv_artifacts import write_manifest
from backend_scout.models import CareerEvidence, Job
from backend_scout.outreach import build_email_review, load_email_review, save_email_review


def _evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {"full_name": "Test Candidate", "email": "candidate@example.com"},
            "skills": [{"category": "Backend", "items": ["Python"]}],
        }
    )


def _manifest(root: Path, company: str, page_id: str) -> None:
    directory = root / company / page_id / "v1"
    directory.mkdir(parents=True)
    docx_path, pdf_path = directory / "cv_draft.docx", directory / "cv_draft.pdf"
    docx_path.write_bytes(b"docx")
    pdf_path.write_bytes(b"pdf")
    write_manifest(directory, page_id, company, "Backend Engineer", docx_path, pdf_path)


def test_email_review_is_recipient_bound_and_defaults_to_approved_pdf(tmp_path: Path) -> None:
    root, records = tmp_path / "archive", tmp_path / "records"
    _manifest(root, "Example", "page-1")
    job = Job(
        source="manual", source_url="https://example.com", company="Example",
        title="Backend Engineer", description="Apply with a CV.",
    )

    first = build_email_review(root, "Example", "page-1", job, _evidence(), "jobs@example.com", "job_post")
    second = build_email_review(root, "Example", "page-1", job, _evidence(), "hr@example.com", "official_site")
    saved = save_email_review(first, records)

    assert first.review_id != second.review_id
    assert saved.suffix == ".json"
    assert load_email_review("page-1", first.review_id, records).attachment_path.endswith(".pdf")


def test_email_review_uses_docx_only_when_post_explicitly_requests_it(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    _manifest(root, "Example", "page-1")
    job = Job(
        source="manual", source_url="https://example.com", company="Example",
        title="Backend Engineer", description="Please attach a DOCX resume.",
    )

    review = build_email_review(root, "Example", "page-1", job, _evidence(), "jobs@example.com", "job_post")

    assert review.attachment_path.endswith(".docx")
