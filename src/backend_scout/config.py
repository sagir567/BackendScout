from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    openai_api_key: str | None = None
    openai_model_fast: str = "gpt-5.6-luna"
    openai_model_balanced: str = "gpt-5.6-terra"
    openai_model_high_quality: str = "gpt-5.6-sol"

    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: str | None = None

    notion_api_key: str | None = None
    notion_api_version: str = "2026-03-11"
    notion_applications_data_source_id: str | None = None

    cv_archive_root: Path = Path("applications")

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
