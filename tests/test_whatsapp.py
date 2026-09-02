from pathlib import Path

from backend_scout.cv_artifacts import write_manifest
from backend_scout.models import CareerEvidence, Job
from backend_scout.whatsapp import (
    build_whatsapp_handoff,
    handoff_url,
    load_whatsapp_handoff,
    save_whatsapp_handoff,
)


def test_whatsapp_handoff_is_prepared_but_not_sent(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    directory = archive / "Example" / "page-1" / "v1"
    directory.mkdir(parents=True)
    docx_path, pdf_path = directory / "cv.docx", directory / "cv.pdf"
    docx_path.write_bytes(b"docx")
    pdf_path.write_bytes(b"pdf")
    write_manifest(directory, "page-1", "Example", "Backend Engineer", docx_path, pdf_path)
    evidence = CareerEvidence.model_validate(
        {"identity": {"full_name": "Test Candidate", "email": "candidate@example.com"}, "skills": [{"category": "Backend", "items": ["Python"]}]}
    )
    job = Job(
        source="manual",
        source_url="https://example.com",
        company="Example",
        title="Backend Engineer",
        description="Apply through the company site.",
    )

    handoff = build_whatsapp_handoff(archive, "Example", "page-1", job, evidence, "972501234567", "job_post")
    save_whatsapp_handoff(handoff, tmp_path / "records")

    assert "web.whatsapp.com/send" in handoff_url(handoff)
    assert "phone=972501234567" in handoff_url(handoff)
    assert load_whatsapp_handoff("page-1", handoff.handoff_id, tmp_path / "records") == handoff
