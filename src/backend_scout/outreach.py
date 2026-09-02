import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend_scout.cv_artifacts import CvDraftManifest, load_manifest, verify_manifest
from backend_scout.models import CareerEvidence, Job

OUTREACH_ROOT = Path("data/outreach")


class EmailReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    notion_page_id: str
    recipient: str
    contact_source: str
    subject: str
    body: str
    attachment_path: str
    draft_id: str
    created_at: datetime


def build_email_review(
    archive_root: Path,
    company: str,
    page_id: str,
    job: Job,
    evidence: CareerEvidence,
    recipient: str,
    contact_source: str,
) -> EmailReview:
    manifest = load_manifest(archive_root, company, page_id)
    verify_manifest(manifest, manifest.draft_id)
    attachment = _attachment_for_job(manifest, job)
    subject = f"Application for {job.title} - {evidence.identity.full_name}"
    body = (
        f"Hello Hiring Team,\n\n"
        f"I am applying for the {job.title} role at {job.company}. "
        f"My background includes backend services, data-intensive systems, and production deployment. "
        f"I have attached my CV for your review.\n\n"
        f"Best regards,\n{evidence.identity.full_name}"
    )
    # A CV draft may have more than one verified delivery route. The review ID
    # must bind the approval button to both the immutable draft and recipient.
    review_id = hashlib.sha256(
        f"{manifest.draft_id}:{recipient.casefold()}".encode()
    ).hexdigest()[:8]
    return EmailReview(
        review_id=review_id,
        notion_page_id=page_id,
        recipient=recipient,
        contact_source=contact_source,
        subject=subject,
        body=body,
        attachment_path=str(attachment),
        draft_id=manifest.draft_id,
        created_at=datetime.now(UTC),
    )


def save_email_review(review: EmailReview, root: Path = OUTREACH_ROOT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{review.notion_page_id}-{review.review_id}.json"
    path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_email_review(page_id: str, review_id: str, root: Path = OUTREACH_ROOT) -> EmailReview:
    path = root / f"{page_id}-{review_id}.json"
    return EmailReview.model_validate_json(path.read_text(encoding="utf-8"))


def _attachment_for_job(manifest: CvDraftManifest, job: Job) -> Path:
    if "docx" in job.description.casefold() or "word document" in job.description.casefold():
        return Path(manifest.docx_path)
    return Path(manifest.pdf_path)
