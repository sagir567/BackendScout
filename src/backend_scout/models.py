from datetime import UTC, datetime
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


APPLICATION_STATUS_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.FOUND: {ApplicationStatus.DIGEST_SENT, ApplicationStatus.CLOSED},
    ApplicationStatus.DIGEST_SENT: {ApplicationStatus.APPROVED_TO_TAILOR, ApplicationStatus.CLOSED},
    ApplicationStatus.APPROVED_TO_TAILOR: {ApplicationStatus.CV_DRAFTED, ApplicationStatus.CLOSED},
    ApplicationStatus.CV_DRAFTED: {ApplicationStatus.APPROVED_TO_SUBMIT, ApplicationStatus.CLOSED},
    ApplicationStatus.APPROVED_TO_SUBMIT: {ApplicationStatus.SUBMITTED, ApplicationStatus.CLOSED},
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.RECRUITER_REPLY,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.RECRUITER_REPLY: {
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
