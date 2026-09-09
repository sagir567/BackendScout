"""Optional private filtering preferences for automatic scouting digests."""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_scout.matcher import ScoredJob
from backend_scout.yaml_files import load_yaml_mapping

DEFAULT_SCOUTING_PREFERENCES_PATH = Path("config/scouting_preferences.yaml")


class ScoutingPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_title_keywords: list[str] = Field(default_factory=lambda: ["backend", "software engineer"])
    maybe_title_keywords: list[str] = Field(default_factory=lambda: ["python", "unity", "ai engineer"])
    excluded_title_keywords: list[str] = Field(
        default_factory=lambda: ["product manager", "data scientist", "devops", "sysadmin", "system administrator"]
    )
    minimum_match_score: int = Field(default=55, ge=0, le=100)
    minimum_role_relevance_points: int = Field(default=15, ge=0, le=25)

    @field_validator("preferred_title_keywords", "maybe_title_keywords", "excluded_title_keywords", mode="before")
    @classmethod
    def normalize_keyword_lists(cls, values: object) -> object:
        if not isinstance(values, list):
            return values
        normalized: list[object] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                normalized.append(value)
                continue
            keyword = " ".join(value.strip().split())
            if keyword and keyword.casefold() not in seen:
                normalized.append(keyword)
                seen.add(keyword.casefold())
        return normalized


def load_scouting_preferences(path: Path = DEFAULT_SCOUTING_PREFERENCES_PATH) -> ScoutingPreferences:
    if not path.exists():
        return ScoutingPreferences()
    return ScoutingPreferences.model_validate(load_yaml_mapping(path))


def public_collection_digest_candidate(scored_job: ScoredJob, preferences: ScoutingPreferences) -> bool:
    if scored_job.result.recommended_action.value not in {"apply", "maybe"}:
        return False
    if scored_job.result.match_score < preferences.minimum_match_score:
        return False
    if _role_relevance_points(scored_job) < preferences.minimum_role_relevance_points:
        return False

    title = _normalize(scored_job.job.title)
    if _contains_any(title, preferences.excluded_title_keywords):
        return False
    if _contains_any(title, preferences.preferred_title_keywords):
        return True
    if _contains_any(title, preferences.maybe_title_keywords):
        return True
    return _role_relevance_points(scored_job) >= 25


def _role_relevance_points(scored_job: ScoredJob) -> int:
    return next(
        (item.points_awarded for item in scored_job.result.score_breakdown if item.component == "role_relevance"),
        0,
    )


def _contains_any(normalized_text: str, keywords: list[str]) -> bool:
    return any(_normalize(keyword) in normalized_text for keyword in keywords if keyword.strip())


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", value.casefold()))
