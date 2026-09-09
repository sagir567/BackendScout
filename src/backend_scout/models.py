from datetime import UTC, datetime
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_required_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError("must not be blank")
    return normalized


def _normalize_optional_string(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    return value.strip() or None


def _normalize_string_list(values: Any) -> Any:
    if not isinstance(values, list):
        return values
    normalized = [value.strip() if isinstance(value, str) else value for value in values]
    normalized = [value for value in normalized if not isinstance(value, str) or value]
    return list(dict.fromkeys(normalized))


class ApplicationStatus(str, Enum):
    FOUND = "found"
    DIGEST_SENT = "digest_sent"
    APPROVED_TO_TAILOR = "approved_to_tailor"
    CV_DRAFTED = "cv_drafted"
    REVISION_REQUESTED = "revision_requested"
    APPROVED_TO_SUBMIT = "approved_to_submit"
    SUBMISSION_PREPARED = "submission_prepared"
    AWAITING_HUMAN_VERIFICATION = "awaiting_human_verification"
    SUBMITTED = "submitted"
    RECRUITER_REPLY = "recruiter_reply"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    CLOSED = "closed"


class MatchRecommendation(str, Enum):
    APPLY = "apply"
    MAYBE = "maybe"
    SKIP = "skip"


class SalaryAssessment(str, Enum):
    ABOVE_FLOOR = "above_floor"
    BELOW_FLOOR = "below_floor"
    UNKNOWN = "unknown"


class LocationAssessment(str, Enum):
    FIT = "fit"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_url: str
    application_url: str | None = None
    company: str
    title: str
    location: str | None = None
    employment_type: str | None = None
    remote_policy: str | None = None
    salary_text: str | None = None
    description: str
    required_skills: list[str] = Field(default_factory=list)
    years_experience: str | None = None
    match_score: int | None = Field(default=None, ge=0, le=100)
    match_reason: str | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source", "source_url", "company", "title", "description", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator(
        "application_url",
        "location",
        "employment_type",
        "remote_policy",
        "salary_text",
        "years_experience",
        "match_reason",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value: Any) -> Any:
        if value is None or not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("required_skills", mode="before")
    @classmethod
    def normalize_skills(cls, values: Any) -> Any:
        if not isinstance(values, list):
            return values

        normalized = []
        for value in values:
            if not isinstance(value, str):
                normalized.append(value)
                continue

            stripped = value.strip()
            if stripped:
                normalized.append(stripped)

        deduped: list[Any] = []
        seen: set[str] = set()
        for value in normalized:
            if not isinstance(value, str):
                deduped.append(value)
                continue

            if value not in seen:
                deduped.append(value)
                seen.add(value)

        return deduped


class WorkPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote: bool = True
    hybrid: bool = True
    onsite: bool = False

    @model_validator(mode="after")
    def require_at_least_one_work_mode(self) -> Self:
        if not (self.remote or self.hybrid or self.onsite):
            raise ValueError("at least one work preference must be enabled")
        return self


class SeniorityPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_years: int = Field(default=0, ge=0)
    max_years: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_valid_year_range(self) -> Self:
        if self.max_years is not None and self.max_years < self.min_years:
            raise ValueError("max_years must be greater than or equal to min_years")
        return self


class ProfileConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_truthful_cv_only: bool = True
    require_approval_before_submit: bool = True
    roles_to_avoid: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def require_safety_constraints(self) -> Self:
        if not self.require_truthful_cv_only:
            raise ValueError("require_truthful_cv_only must stay true")
        if not self.require_approval_before_submit:
            raise ValueError("require_approval_before_submit must stay true")
        return self


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    target_roles: list[str] = Field(min_length=1)
    target_locations: list[str] = Field(default_factory=list)
    salary_floor_nis: int = Field(ge=0)
    work_preferences: WorkPreferences = Field(default_factory=WorkPreferences)
    seniority: SeniorityPreference = Field(default_factory=SeniorityPreference)
    core_skills: list[str] = Field(min_length=1)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    proof_points: list[str] = Field(default_factory=list)
    constraints: ProfileConstraints = Field(default_factory=ProfileConstraints)

    @field_validator(
        "target_roles",
        "target_locations",
        "core_skills",
        "nice_to_have_skills",
        "proof_points",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, values: Any) -> Any:
        if not isinstance(values, list):
            return values

        normalized = []
        for value in values:
            if not isinstance(value, str):
                normalized.append(value)
                continue

            stripped = value.strip()
            if stripped:
                normalized.append(stripped)

        deduped: list[Any] = []
        seen: set[str] = set()
        for value in normalized:
            if not isinstance(value, str):
                deduped.append(value)
                continue

            if value not in seen:
                deduped.append(value)
                seen.add(value)

        return deduped

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class EvidenceBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str

    _normalize_id = field_validator("id", mode="before")(_normalize_required_string)
    _normalize_text = field_validator("text", mode="before")(_normalize_required_string)


class SkillGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    items: list[str] = Field(min_length=1)

    _normalize_category = field_validator("category", mode="before")(_normalize_required_string)
    _normalize_items = field_validator("items", mode="before")(_normalize_string_list)


class ExperienceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    organization: str
    title: str
    start_date: str
    end_date: str
    location: str | None = None
    bullets: list[EvidenceBullet] = Field(min_length=1)

    _normalize_id = field_validator("id", mode="before")(_normalize_required_string)
    _normalize_organization = field_validator("organization", mode="before")(
        _normalize_required_string
    )
    _normalize_title = field_validator("title", mode="before")(_normalize_required_string)
    _normalize_start_date = field_validator("start_date", mode="before")(_normalize_required_string)
    _normalize_end_date = field_validator("end_date", mode="before")(_normalize_required_string)
    _normalize_location = field_validator("location", mode="before")(_normalize_optional_string)


class ProjectEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    link: str | None = None
    bullets: list[EvidenceBullet] = Field(min_length=1)

    _normalize_id = field_validator("id", mode="before")(_normalize_required_string)
    _normalize_name = field_validator("name", mode="before")(_normalize_required_string)
    _normalize_link = field_validator("link", mode="before")(_normalize_optional_string)


class EducationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str
    credential: str
    end_date: str | None = None

    _normalize_institution = field_validator("institution", mode="before")(_normalize_required_string)
    _normalize_credential = field_validator("credential", mode="before")(_normalize_required_string)
    _normalize_end_date = field_validator("end_date", mode="before")(_normalize_optional_string)


class PublicationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    venue: str
    year: str

    _normalize_title = field_validator("title", mode="before")(_normalize_required_string)
    _normalize_venue = field_validator("venue", mode="before")(_normalize_required_string)
    _normalize_year = field_validator("year", mode="before")(_normalize_required_string)


class CandidateIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)

    _normalize_name = field_validator("full_name", mode="before")(_normalize_required_string)
    _normalize_email = field_validator("email", mode="before")(_normalize_required_string)
    _normalize_phone = field_validator("phone", mode="before")(_normalize_optional_string)
    _normalize_location = field_validator("location", mode="before")(_normalize_optional_string)
    _normalize_links = field_validator("links", mode="before")(_normalize_string_list)


class CareerEvidence(BaseModel):
    """Private factual material that is allowed to appear in a tailored CV."""

    model_config = ConfigDict(extra="forbid")

    identity: CandidateIdentity
    summary: str | None = None
    skills: list[SkillGroup] = Field(min_length=1)
    experience: list[ExperienceEvidence] = Field(default_factory=list)
    projects: list[ProjectEvidence] = Field(default_factory=list)
    education: list[EducationEvidence] = Field(default_factory=list)
    publications: list[PublicationEvidence] = Field(default_factory=list)

    _normalize_summary = field_validator("summary", mode="before")(_normalize_optional_string)

    @model_validator(mode="after")
    def require_unique_evidence_ids(self) -> Self:
        evidence_ids = [item.id for item in self.experience]
        evidence_ids.extend(item.id for item in self.projects)
        evidence_ids.extend(
            bullet.id for item in self.experience for bullet in item.bullets
        )
        evidence_ids.extend(bullet.id for item in self.projects for bullet in item.bullets)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("career evidence IDs must be unique")
        return self

    def evidence_ids(self) -> set[str]:
        ids = {item.id for item in self.experience}
        ids.update(item.id for item in self.projects)
        ids.update(bullet.id for item in self.experience for bullet in item.bullets)
        ids.update(bullet.id for item in self.projects for bullet in item.bullets)
        return ids


class CvStyle(BaseModel):
    """Private presentation rules applied to every generated CV."""

    model_config = ConfigDict(extra="forbid")

    include_headline: bool = False
    target_page_count: int = Field(default=1, ge=1, le=1)
    show_raw_urls: bool = False
    link_labels: dict[str, str] = Field(default_factory=dict)
    writing_rules: list[str] = Field(default_factory=list)
    tailoring_guidance: list[str] = Field(default_factory=list)

    _normalize_writing_rules = field_validator("writing_rules", mode="before")(
        _normalize_string_list
    )
    _normalize_tailoring_guidance = field_validator("tailoring_guidance", mode="before")(
        _normalize_string_list
    )

    @field_validator("link_labels", mode="before")
    @classmethod
    def normalize_link_labels(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            str(url).strip(): str(label).strip()
            for url, label in value.items()
            if str(url).strip() and str(label).strip()
        }

    @model_validator(mode="after")
    def require_hidden_raw_urls(self) -> Self:
        if self.show_raw_urls:
            raise ValueError("show_raw_urls must stay false")
        return self


class TailoredBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str] = Field(min_length=1)

    _normalize_text = field_validator("text", mode="before")(_normalize_required_string)
    _normalize_evidence_ids = field_validator("evidence_ids", mode="before")(
        _normalize_string_list
    )


class TailoredExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    bullets: list[TailoredBullet] = Field(min_length=1)

    _normalize_evidence_id = field_validator("evidence_id", mode="before")(_normalize_required_string)


class TailoredProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    bullets: list[TailoredBullet] = Field(min_length=1)

    _normalize_evidence_id = field_validator("evidence_id", mode="before")(_normalize_required_string)


class TailoredCv(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str | None = None
    summary: str | None = None
    summary_evidence_ids: list[str] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[TailoredExperience] = Field(default_factory=list)
    projects: list[TailoredProject] = Field(default_factory=list)
    education: list[EducationEvidence] = Field(default_factory=list)
    publications: list[PublicationEvidence] = Field(default_factory=list)

    _normalize_headline = field_validator("headline", mode="before")(_normalize_optional_string)
    _normalize_summary = field_validator("summary", mode="before")(_normalize_optional_string)
    _normalize_summary_evidence_ids = field_validator("summary_evidence_ids", mode="before")(
        _normalize_string_list
    )


class ManualJobImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[Job] = Field(min_length=1)


class ScoreBreakdownItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    points_awarded: int = Field(ge=0)
    points_max: int = Field(ge=1)
    reason: str

    @model_validator(mode="after")
    def require_points_within_component_max(self) -> Self:
        if self.points_awarded > self.points_max:
            raise ValueError("points_awarded must be less than or equal to points_max")
        return self


class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_score: int = Field(ge=0, le=100)
    recommended_action: MatchRecommendation
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    score_breakdown: list[ScoreBreakdownItem] = Field(min_length=1)
    salary_assessment: SalaryAssessment
    location_assessment: LocationAssessment
    reason_summary: str

    @field_validator(
        "matched_skills",
        "missing_skills",
        "strengths",
        "concerns",
        mode="before",
    )
    @classmethod
    def normalize_match_lists(cls, values: Any) -> Any:
        return CandidateProfile.normalize_string_lists(values)

    @field_validator("reason_summary", mode="before")
    @classmethod
    def normalize_reason_summary(cls, value: Any) -> Any:
        return Job.normalize_required_strings(value)

    @model_validator(mode="after")
    def require_score_breakdown_to_match_total(self) -> Self:
        breakdown_total = sum(item.points_awarded for item in self.score_breakdown)
        if breakdown_total != self.match_score:
            raise ValueError("match_score must equal the sum of score_breakdown points")
        return self


class CvEvidenceCoverage(BaseModel):
    """How well the factual evidence ledger covers a job's explicit requirements."""

    model_config = ConfigDict(extra="forbid")

    coverage_score: int = Field(ge=0, le=100)
    target_score: int = Field(default=90, ge=1, le=100)
    covered_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    summary: str

    @field_validator(
        "covered_requirements",
        "missing_requirements",
        "supporting_evidence_ids",
        "clarification_questions",
        mode="before",
    )
    @classmethod
    def normalize_coverage_lists(cls, values: Any) -> Any:
        return CandidateProfile.normalize_string_lists(values)

    _normalize_summary = field_validator("summary", mode="before")(_normalize_required_string)

    @model_validator(mode="after")
    def require_coverage_lists_to_match_score(self) -> Self:
        total = len(self.covered_requirements) + len(self.missing_requirements)
        if total and self.coverage_score != round((len(self.covered_requirements) / total) * 100):
            raise ValueError("coverage_score must match covered and missing requirement counts")
        return self


APPLICATION_STATUS_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.FOUND: {ApplicationStatus.DIGEST_SENT, ApplicationStatus.CLOSED},
    ApplicationStatus.DIGEST_SENT: {ApplicationStatus.APPROVED_TO_TAILOR, ApplicationStatus.CLOSED},
    ApplicationStatus.APPROVED_TO_TAILOR: {ApplicationStatus.CV_DRAFTED, ApplicationStatus.CLOSED},
    ApplicationStatus.CV_DRAFTED: {
        ApplicationStatus.REVISION_REQUESTED,
        ApplicationStatus.APPROVED_TO_SUBMIT,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.REVISION_REQUESTED: {ApplicationStatus.APPROVED_TO_TAILOR, ApplicationStatus.CLOSED},
    ApplicationStatus.APPROVED_TO_SUBMIT: {
        ApplicationStatus.SUBMISSION_PREPARED,
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.SUBMISSION_PREPARED: {
        ApplicationStatus.AWAITING_HUMAN_VERIFICATION,
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.AWAITING_HUMAN_VERIFICATION: {
        ApplicationStatus.SUBMISSION_PREPARED,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.RECRUITER_REPLY,
        ApplicationStatus.ASSESSMENT,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.RECRUITER_REPLY: {
        ApplicationStatus.ASSESSMENT,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.ASSESSMENT: {
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.INTERVIEW: {
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.REJECTED: {ApplicationStatus.CLOSED},
    ApplicationStatus.OFFER: {ApplicationStatus.CLOSED},
    ApplicationStatus.CLOSED: set(),
}


def can_transition_application_status(
    current_status: ApplicationStatus,
    next_status: ApplicationStatus,
) -> bool:
    if current_status == next_status:
        return True

    return next_status in APPLICATION_STATUS_TRANSITIONS[current_status]


def validate_application_status_transition(
    current_status: ApplicationStatus,
    next_status: ApplicationStatus,
) -> None:
    if can_transition_application_status(current_status, next_status):
        return

    raise ValueError(
        f"Cannot transition application status from {current_status.value} to {next_status.value}"
    )


class Application(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notion_page_id: str | None = None
    job: Job
    status: ApplicationStatus = Field(default=ApplicationStatus.FOUND)
    company_folder: str | None = None
    tailored_cv_path: str | None = None
    last_action_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str | None = None

    def validate_status_transition(self, next_status: ApplicationStatus) -> None:
        validate_application_status_transition(self.status, next_status)


class ApplicationDigestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notion_page_id: str
    company: str
    title: str
    status: ApplicationStatus
    source: str
    source_url: str
    application_url: str | None = None
    location: str | None = None
    remote_policy: str | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    match_score: int | None = Field(default=None, ge=0, le=100)
    match_reason: str | None = None
    required_skills: list[str] = Field(default_factory=list)
