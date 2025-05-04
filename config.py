"""
config.py
~~~~~~~~~~~~~~~~~~~~~~

Centralised configuration loader for the Feedback-Bot project.
───────────────────────────────────────────────────────────────
• Reads variables from a .env file (via Pydantic BaseSettings).
• Caches the Settings instance so other modules can simply:
      from feedback_bot.config import settings
• Converts CURATORS into a Python *set* that can be checked by either
  numeric Telegram ID or lower-cased @username.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Set, Union

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # ── Telegram & roles ───────────────────────────────────────
    bot_token: str = Field(..., env="BOT_TOKEN")
    admins: str = Field(default="", env="ADMINS")

    # ── Persistence ───────────────────────────────────────────
    database_url: str = Field(
        "postgresql+asyncpg://user:password@host:port/dbname", env="DATABASE_URL"
    )

    # ── Google Sheets ─────────────────────────────────────────
    gsheet_id: str = Field(..., env="GSHEET_ID")
    google_credentials_path: str = Field(
        "credentials.json", env="GOOGLE_CREDENTIALS_PATH"
    )

    # ── Pydantic config ───────────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Helper properties ─────────────────────────────────────
    @property
    def admin_id_set(self) -> Set[Union[int, str]]:
        """
        Normalise ADMINS into a hashable set similar to curators.
        """
        acc: set[Union[int, str]] = set()
        for raw in self.admins.split(','):
            item = raw.strip()
            if not item:
                continue
            if item.startswith("@"):
                acc.add(item.lower())
            else:
                try:
                    acc.add(int(item))
                except ValueError:
                    continue
        return acc


@lru_cache(maxsize=1)
def get_settings() -> Settings:  # lazy singleton
    return Settings()


# convenient shortcut so other modules can just `from config import settings`
settings: Settings = get_settings()
