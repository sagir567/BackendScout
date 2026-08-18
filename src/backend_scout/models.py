from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class ApplicationStatus(str, Enum):
    FOUND = "found"
    DIGEST_SENT = "digest_sent"
    APPROVED_TO_TAILOR = "approved_to_tailor"
    CV_DRAFTED = "cv_drafted"
    APPROVED_TO_SUBMIT = "approved_to_submit"
    SUBMITTED = "submitted"
    RECRUITER_REPLY = "recruiter_reply"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    CLOSED = "closed"


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str
    source_url: str = Field(index=True, unique=True)
    company: str
    title: str
    location: str | None = None
    employment_type: str | None = None
    remote_policy: str | None = None
    salary_text: str | None = None
    description: str
    required_skills: str | None = None
    years_experience: str | None = None
    match_score: int | None = Field(default=None, ge=0, le=100)
    match_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Application(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    status: ApplicationStatus = Field(default=ApplicationStatus.FOUND)
    company_folder: str | None = None
    tailored_cv_path: str | None = None
    last_action_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str | None = None

