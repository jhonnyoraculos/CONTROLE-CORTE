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


def _config_value(key: str, default: str = "") -> str:
    value = os.getenv(key, "")
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets.get(key, default))
    except (FileNotFoundError, RuntimeError, KeyError):
        return default


def get_initial_admin() -> tuple[str, str, str] | None:
    """One-time credentials supplied outside Git for a cloud deployment."""
    email = _config_value("INITIAL_ADMIN_EMAIL")
    password = _config_value("INITIAL_ADMIN_PASSWORD")
    if not email or not password:
        return None
    return _config_value("INITIAL_ADMIN_NAME", "Administrador"), email, password


def get_settings() -> Settings:
    url = _config_value("DATABASE_URL")
    return Settings(
        database_url=url,
        max_upload_mb=int(os.getenv("APP_MAX_UPLOAD_MB", "20")),
        timezone=os.getenv("APP_TIMEZONE", "America/Sao_Paulo"),
    )
