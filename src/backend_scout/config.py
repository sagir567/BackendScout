from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TrackerName(str, Enum):
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    openai_api_key: str | None = None
    openai_model_fast: str = "gpt-5.6-luna"
    openai_model_balanced: str = "gpt-5.6-terra"
    openai_model_high_quality: str = "gpt-5.6-sol"

    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: str | None = None
    telegram_default_chat_id: int | None = None

    notion_api_key: str | None = None
    notion_api_version: str = "2026-03-11"
    # Legacy test-tracker value. Keep it during the two-tracker migration.
    notion_applications_data_source_id: str | None = None
    notion_test_applications_data_source_id: str | None = None
    notion_production_applications_data_source_id: str | None = None

    cv_archive_root: Path = Path("applications")
    cv_require_coverage_target: bool = True
    gmail_oauth_client_secret_path: Path | None = None
    gmail_credential_store: Literal["keyring", "file"] = "keyring"
    gmail_credential_file: Path | None = None
    browser_profile_root: Path = Path("browser-profile")
    submission_proof_root: Path = Path("data/submission_proofs")
    workflow_database_path: Path = Path("data/runtime/backendscout.sqlite3")
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_public_origin: str = "http://127.0.0.1:8765"
    web_cookie_secure: bool = False
    web_allow_delivery: bool = False
    portal_delivery_mode: Literal["legacy", "hybrid"] = "legacy"
    portal_hybrid_rollout: Literal["test", "canary", "enabled"] = "test"
    portal_canary_application_ids: str = ""
    portal_receipt_timeout_seconds: float = Field(default=90.0, gt=0, le=90)
    portal_ai_prices_path: Path | None = None
    portal_ai_monthly_limit_nis: str = "40"
    portal_ai_application_limit_nis: str = "2"
    project_monthly_limit_nis: str = "100"
    project_fixed_monthly_nis: str = "0"
    portal_max_model_calls: int = Field(default=6, ge=0, le=6)
    portal_max_actions: int = Field(default=40, ge=1, le=40)
    portal_max_active_seconds: float = Field(default=600.0, gt=0, le=600)
    verification_handoff_enabled: bool = True
    verification_handoff_port: int = 0
    remote_desktop_instructions: str = (
        "Open Chrome Remote Desktop on your phone and complete the human verification "
        "in the prepared browser."
    )

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def normalize_telegram_allowed_user_ids(cls, value: str | None) -> str | None:
        if value is None or not isinstance(value, str):
            return value
        normalized = ",".join(
            part.strip() for part in value.split(",") if part.strip()
        )
        return normalized or None

    @field_validator("portal_ai_monthly_limit_nis", "portal_ai_application_limit_nis",
                     "project_monthly_limit_nis", "project_fixed_monthly_nis")
    @classmethod
    def validate_budget_amount(cls, value, info):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ValueError("Budget must be a decimal NIS amount") from None
        ceiling = 40 if info.field_name.startswith("portal_") else 100
        if not amount.is_finite() or amount < 0 or amount > ceiling:
            raise ValueError("Budget exceeds the approved ceiling or is invalid")
        return value

    @property
    def telegram_allowed_user_id_set(self) -> set[int]:
        if not self.telegram_allowed_user_ids:
            return set()

        values: set[int] = set()
        for raw_value in self.telegram_allowed_user_ids.split(","):
            values.add(int(raw_value))
        return values

    def applications_data_source_id_for(self, tracker: TrackerName) -> str | None:
        if tracker == TrackerName.PRODUCTION:
            return self.notion_production_applications_data_source_id
        return self.notion_test_applications_data_source_id or self.notion_applications_data_source_id
