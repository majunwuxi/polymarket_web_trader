from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class WalletConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1,max_length=100)
    wallet_address: str
    signer_address: str
    wallet_type: Literal['EOA','POLY_PROXY','GNOSIS_SAFE','DEPOSIT_WALLET']
    credential_ref: str | None = Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,64}$')

    @field_validator('wallet_address','signer_address')
    @classmethod
    def validate_address(cls, value: str) -> str:
        import re
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}',value): raise ValueError('invalid wallet address')
        return value.lower()


class WalletSecrets(BaseModel):
    model_config = ConfigDict(extra='forbid',hide_input_in_errors=True)
    private_key: SecretStr
    clob_api_key: SecretStr | None = None
    clob_api_secret: SecretStr | None = None
    clob_api_passphrase: SecretStr | None = None
    builder_api_key: SecretStr | None = None
    builder_api_secret: SecretStr | None = None
    builder_api_passphrase: SecretStr | None = None

    @model_validator(mode='after')
    def complete_clob_credentials(self):
        values=[x.get_secret_value() if x else '' for x in (self.clob_api_key,self.clob_api_secret,self.clob_api_passphrase)]
        if any(values) and not all(values): raise ValueError('CLOB credentials must all be set or all omitted')
        builder=[x.get_secret_value() if x else '' for x in (self.builder_api_key,self.builder_api_secret,self.builder_api_passphrase)]
        if any(builder) and not all(builder): raise ValueError('Builder credentials must all be set or all omitted')
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYSPORTS_", env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    auth_secret: SecretStr
    public_origin: str
    api_football_key: SecretStr | None = None
    balldontlie_key: SecretStr | None = None
    wallets: list[WalletConfig] = Field(default_factory=list)
    wallet_credentials: dict[str, WalletSecrets] = Field(default_factory=dict,repr=False)
    polygon_rpc_url: SecretStr | None = None
    combo_execution_enabled: bool = False

    @model_validator(mode="after")
    def unique_wallets(self):
        addresses=[wallet.wallet_address for wallet in self.wallets]
        if len(addresses)!=len(set(addresses)): raise ValueError("duplicate wallet address")
        return self

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
