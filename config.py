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

    db_user: str = Field(default="", env="DB_USER")
    db_user_pwd: str = Field(default="", env="DB_USER_PWD")

    # ── Persistence ───────────────────────────────────────────
    database_url: str = Field(
        "postgresql+asyncpg://user:password@host:port/dbname", env="DATABASE_URL"
    )

    # ── Google Sheets ─────────────────────────────────────────
    gsheet_id: str = Field(..., env="GSHEET_ID")
    google_credentials_path: str = Field(
        "/Users/nikitayusupov/Desktop/sd_feedback_bot/Telegram-Feedback-bot/gen-lang-client-0435735157-04b6dc045b7b.json", env="GOOGLE_CREDENTIALS_PATH"
    )
    
    # Default hardcoded values if not set in .env
    gsheet_url: str = Field(
        "https://docs.google.com/spreadsheets/d/1WIVam6ODzM0KHqmvb2b0yXCrnX41Juy6fMC7axnqv7U/edit?gid=0#gid=0", 
        env="GSHEET_URL"
    )
    gsheet_tab_name: str = Field("feedback", env="GSHEET_TAB_NAME")
    
    # Survey responses Google Sheets settings
    surveys_gsheet_url: str = Field(
        "https://docs.google.com/spreadsheets/d/1qj8vnUw3JVy9RzakT7_CvD6h-SHPSjLsB-lLrlP8IpA/edit?usp=sharing", 
        env="SURVEYS_GSHEET_URL"
    )
    surveys_gsheet_tab_name: str = Field("survey_responses", env="SURVEYS_GSHEET_TAB_NAME")

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


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached instance of Settings.
    """
    return Settings()


# convenient shortcut so other modules can just `from config import settings`
settings = get_settings()
