from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYSPORTS_", env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    auth_secret: SecretStr
    public_origin: str
    api_football_key: SecretStr | None = None
    balldontlie_key: SecretStr | None = None

    @field_validator("auth_secret")
    @classmethod
    def validate_auth_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32 or value.get_secret_value().startswith("CHANGE_ME"):
            raise ValueError("AUTH_SECRET must contain at least 32 bytes")
        return value

    @field_validator("public_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("PUBLIC_ORIGIN must be an HTTPS origin without path")
        return value.rstrip("/")

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
