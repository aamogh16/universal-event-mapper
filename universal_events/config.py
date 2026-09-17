"""Runtime configuration, loaded from .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
MAPPINGS_DIR = PACKAGE_ROOT / "mappings"
FLOWS_DIR = PROJECT_ROOT / "composer" / "flows"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Klaviyo ---
    klaviyo_private_api_key: str = ""
    klaviyo_dry_run: bool = False
    # Pinned deliberately. Klaviyo's API is versioned by date and an unset or
    # stale revision changes response shapes, so we send it explicitly.
    klaviyo_api_revision: str = "2026-07-15"
    klaviyo_base_url: str = "https://a.klaviyo.com"

    # --- LLM providers ---
    # openai is primary: Tier 1 allows 500 RPM vs Gemini free tier's 10 RPM,
    # which matters more than price for a live demo.
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_audit_model: str = "gpt-5.4-mini"

    gemini_api_key: str = ""
    gemini_mapping_model: str = "gemini-3.8-flash"
    gemini_agent_model: str = "gemini-3.5-flash-lite"

    # --- demo controls ---
    sweep_interval_seconds: int = 60
    rehearse_mode: bool = False

    event_db_path: Path = PROJECT_ROOT / "events.db"

    @property
    def klaviyo_configured(self) -> bool:
        key = self.klaviyo_private_api_key
        return bool(key) and not key.startswith("pk_replace")

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def active_provider(self) -> str:
        """Which auditor actually runs, accounting for missing keys.

        Never raises and never leaves the caller without a strategy: if no
        provider is usable we fall back to the deterministic rules auditor so
        the demo still produces a proposal.
        """
        if self.rehearse_mode:
            return "rehearse"
        if self.llm_provider == "openai" and self.openai_configured:
            return "openai"
        if self.llm_provider == "gemini" and self.gemini_configured:
            return "gemini"
        if self.llm_provider == "rules":
            return "rules"
        if self.openai_configured:
            return "openai"
        if self.gemini_configured:
            return "gemini"
        return "rules"


settings = Settings()
