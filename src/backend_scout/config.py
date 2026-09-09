from enum import Enum
from pathlib import Path

from pydantic import field_validator
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
    gmail_oauth_client_secret_path: Path | None = None
    browser_profile_root: Path = Path("browser-profile")
    submission_proof_root: Path = Path("data/submission_proofs")
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
