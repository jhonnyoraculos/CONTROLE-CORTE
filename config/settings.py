"""Application configuration. Secrets never belong in source control."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str
    max_upload_mb: int = 20
    timezone: str = "America/Sao_Paulo"


def get_settings() -> Settings:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        try:
            import streamlit as st

            url = st.secrets.get("DATABASE_URL", "")
        except (FileNotFoundError, RuntimeError, KeyError):
            pass
    return Settings(
        database_url=url,
        max_upload_mb=int(os.getenv("APP_MAX_UPLOAD_MB", "20")),
        timezone=os.getenv("APP_TIMEZONE", "America/Sao_Paulo"),
    )
