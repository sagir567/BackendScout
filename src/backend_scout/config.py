from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    openai_api_key: str | None = None
    openai_model_fast: str = "gpt-5.6-luna"
    openai_model_balanced: str = "gpt-5.6-terra"
    openai_model_high_quality: str = "gpt-5.6-sol"

    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: str | None = None

    db_path: Path = Path("data/backend_scout.sqlite")
    cv_archive_root: Path = Path("/Users/sagi/Documents/CV")

