"""不访问上游：所有环境参数均为模拟配置。"""
import socket

import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.config import Settings, get_settings
from backend.main import create_app

MOCK_AUTH_SECRET = "mock-only-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
MOCK_DATABASE_URL = "postgresql+psycopg://mock_user:mock_password@127.0.0.1:5432/polysports_test_mock"


def test_config_requires_postgresql_and_redacts_password():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///not-postgres", auth_secret=MOCK_AUTH_SECRET, public_origin="https://example.test")
    with pytest.raises(ValidationError) as invalid:
        Settings(_env_file=None, database_url="sqlite://secret_password@bad", auth_secret=MOCK_AUTH_SECRET, public_origin="https://example.test")
    assert "secret_password" not in str(invalid.value)
    config = Settings(_env_file=None, database_url=MOCK_DATABASE_URL, auth_secret=MOCK_AUTH_SECRET, public_origin="https://example.test", environment="test")
    assert "mock_password" not in repr(config)


def test_missing_config_fails(monkeypatch):
    monkeypatch.delenv("POLYSPORTS_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_health_contract_and_request_id(monkeypatch):
    monkeypatch.setenv("POLYSPORTS_DATABASE_URL", MOCK_DATABASE_URL)
    monkeypatch.setenv("POLYSPORTS_AUTH_SECRET", MOCK_AUTH_SECRET)
    monkeypatch.setenv("POLYSPORTS_PUBLIC_ORIGIN", "https://example.test")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/health/live", headers={"X-Request-ID": "00000000-0000-4000-8000-000000000001"})
        assert response.status_code == 200
        assert response.json()["data"] == {"status": "ok"}
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert "mock_password" not in response.text
    finally:
        get_settings.cache_clear()


def test_production_trading_network_is_blocked():
    with pytest.raises(RuntimeError, match="禁止外部网络"):
        socket.create_connection(("clob.polymarket.com", 443), timeout=1)


def test_http_client_cannot_access_production_trading():
    with pytest.raises(RuntimeError, match="禁止外部网络"):
        httpx.get("https://clob.polymarket.com/", trust_env=False, timeout=1)


def test_systemd_credential_file_is_used_and_missing_fails_closed(monkeypatch,tmp_path):
    for name in ('POLYSPORTS_DATABASE_URL','POLYSPORTS_AUTH_SECRET','POLYSPORTS_PUBLIC_ORIGIN','POLYSPORTS_WALLETS'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('CREDENTIALS_DIRECTORY',str(tmp_path))
    path=tmp_path/'polysports.env'
    # 明确标识的模拟值，非真实凭据。
    path.write_text("POLYSPORTS_DATABASE_URL="+MOCK_DATABASE_URL+"\nPOLYSPORTS_AUTH_SECRET="+MOCK_AUTH_SECRET+"\nPOLYSPORTS_PUBLIC_ORIGIN=https://credential.example.test\nPOLYSPORTS_WALLETS='[]'\n",encoding='utf-8')
    get_settings.cache_clear()
    try:
        monkeypatch.setenv('POLYSPORTS_PUBLIC_ORIGIN','https://untrusted-environment.test')
        assert get_settings().public_origin=='https://credential.example.test'
        assert get_settings().wallets==[]
        path.unlink();get_settings.cache_clear()
        monkeypatch.setenv('POLYSPORTS_DATABASE_URL',MOCK_DATABASE_URL)
        with pytest.raises(RuntimeError,match='凭据缺失'):get_settings()
    finally:get_settings.cache_clear()
