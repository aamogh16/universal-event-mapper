"""Runtime configuration, loaded from .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
MAPPINGS_DIR = PACKAGE_ROOT / "mappings"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    klaviyo_private_api_key: str = ""
    klaviyo_dry_run: bool = False

    # Pinned deliberately. Klaviyo's API is versioned by date and an unset or
    # stale revision changes response shapes, so we send it explicitly.
    klaviyo_api_revision: str = "2026-07-15"
    klaviyo_base_url: str = "https://a.klaviyo.com"

    gemini_api_key: str = ""
    gemini_mapping_model: str = "gemini-3.8-flash"
    gemini_agent_model: str = "gemini-3.5-flash-lite"

    event_db_path: Path = PROJECT_ROOT / "events.db"

    @property
    def klaviyo_configured(self) -> bool:
        return bool(self.klaviyo_private_api_key) and not self.klaviyo_private_api_key.startswith(
            "pk_replace"
        )

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
