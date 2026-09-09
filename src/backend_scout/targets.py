"""Private target-company configuration for public ATS collection."""

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_scout.yaml_files import load_yaml_mapping

DEFAULT_TARGET_COMPANIES_PATH = Path("config/target_companies.yaml")


class AtsProvider(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class TargetCompany(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: AtsProvider
    board_token: str
    enabled: bool = True
    notes: str | None = None

    @field_validator("name", "board_token", mode="before")
    @classmethod
    def require_text(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class TargetCompanies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[TargetCompany] = Field(min_length=1)


def load_target_companies(path: Path = DEFAULT_TARGET_COMPANIES_PATH) -> TargetCompanies:
    return TargetCompanies.model_validate(load_yaml_mapping(path))
