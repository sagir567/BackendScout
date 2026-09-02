"""Prepared-only WhatsApp handoffs; this module never sends a message."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

from backend_scout.cv_artifacts import load_manifest, verify_manifest
from backend_scout.models import CareerEvidence, Job

WHATSAPP_ROOT = Path("data/whatsapp")


class WhatsAppHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_id: str
    notion_page_id: str
    recipient: str
    contact_source: str
    body: str
    attachment_path: str
    draft_id: str
    created_at: datetime


def build_whatsapp_handoff(
    archive_root: Path,
    company: str,
    page_id: str,
    job: Job,
    evidence: CareerEvidence,
    recipient: str,
    contact_source: str,
) -> WhatsAppHandoff:
    manifest = load_manifest(archive_root, company, page_id)
    verify_manifest(manifest, manifest.draft_id)
    handoff_id = hashlib.sha256(f"{manifest.draft_id}:{recipient}".encode()).hexdigest()[:8]
    body = (
        f"Hello, I am applying for the {job.title} role at {job.company}. "
        f"I have attached my CV for your review. Thank you, {evidence.identity.full_name}."
    )
    return WhatsAppHandoff(
        handoff_id=handoff_id,
        notion_page_id=page_id,
        recipient=recipient.lstrip("+"),
        contact_source=contact_source,
        body=body,
        attachment_path=manifest.pdf_path,
        draft_id=manifest.draft_id,
        created_at=datetime.now(UTC),
    )


def handoff_url(handoff: WhatsAppHandoff) -> str:
    return f"https://web.whatsapp.com/send?{urlencode({'phone': handoff.recipient, 'text': handoff.body})}"


def save_whatsapp_handoff(handoff: WhatsAppHandoff, root: Path = WHATSAPP_ROOT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{handoff.notion_page_id}-{handoff.handoff_id}.json"
    path.write_text(handoff.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_whatsapp_handoff(
    page_id: str, handoff_id: str, root: Path = WHATSAPP_ROOT
) -> WhatsAppHandoff:
    path = root / f"{page_id}-{handoff_id}.json"
    return WhatsAppHandoff.model_validate_json(path.read_text(encoding="utf-8"))
