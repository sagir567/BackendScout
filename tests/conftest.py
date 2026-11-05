"""Synthetic fixtures for the isolated mobile/service path; never load .env."""

import pytest

from backend_scout.application_service import ApplicationService
from backend_scout.application_store import ApplicationStore
from backend_scout.config import Settings, TrackerName
from backend_scout.cv_artifacts import write_manifest
from backend_scout.models import CandidateIdentity, CareerEvidence, Job


@pytest.fixture
def review_service(tmp_path):
    private = tmp_path / "private"
    settings = Settings(
        _env_file=None,
        cv_archive_root=private / "cv_archive",
        workflow_database_path=private / "workflow.sqlite3",
        telegram_allowed_user_ids="12345",
        web_allow_delivery=True,
    )
    store = ApplicationStore(settings.workflow_database_path, private)
    job = Job(
        source="fixture",
        source_url="https://example.test/job/1",
        application_url="https://jobs.ashbyhq.com/synthetic/fixture/application",
        company="Example",
        title="Backend Engineer",
        location="Israel",
        years_experience="2+ years",
        description="Requirements: build Python backend APIs with Docker, including tests and documented deployment workflows.",
        required_skills=["Python", "Docker"],
        match_score=85,
    )
    store.import_snapshot("example-job", TrackerName.TEST, job, "cv_drafted")
    directory = settings.cv_archive_root / "Example" / "example-job" / "v1"
    directory.mkdir(parents=True)
    docx, pdf = directory / "Test Candidate CV.docx", directory / "Test Candidate CV.pdf"
    docx.write_bytes(b"Synthetic DOCX")
    pdf.write_bytes(b"%PDF-1.4\nSynthetic fixture only")
    write_manifest(directory, "example-job", "Example", job.title, docx, pdf, "test")
    evidence = CareerEvidence.model_construct(
        identity=CandidateIdentity(
            full_name="Test Candidate", email="fixture@example.test", phone="0500000000"
        ),
        skills=[],
    )
    return ApplicationService(store, settings, identity_loader=lambda: evidence)
