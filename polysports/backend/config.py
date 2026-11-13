from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYSPORTS_", env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"

    @field_validator("database_url")
    @classmethod
    def validate_postgresql_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = url.drivername == "postgresql+psycopg" and bool(url.host) and bool(url.database) and bool(url.username)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("DATABASE_URL must be a postgresql+psycopg URL with host, user and database")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
