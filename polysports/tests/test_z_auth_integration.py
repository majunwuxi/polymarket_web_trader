"""模拟账户和密码；仅在隔离的本机 PostgreSQL 测试库运行。"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.auth import LOGIN_COOKIE, SESSION_COOKIE, hasher
from backend.config import get_settings
from backend.main import create_app

MOCK_PASSWORD = "mock-password-12345"
MOCK_SECRET = "mock-only-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
ORIGIN = "https://example.test"

@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("POLYSPORTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("未提供隔离 PostgreSQL；未执行认证集成测试")
    url = make_url(dsn)
    if url.drivername != "postgresql+psycopg" or url.host not in ("127.0.0.1", "localhost", "::1") or not (url.database or "").startswith("polysports_test_"):
        pytest.fail("认证测试仅允许本机隔离测试数据库")
    monkeypatch.setenv("POLYSPORTS_DATABASE_URL", dsn)
    monkeypatch.setenv("POLYSPORTS_AUTH_SECRET", MOCK_SECRET)
    monkeypatch.setenv("POLYSPORTS_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("POLYSPORTS_ENVIRONMENT", "test")
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    engine=create_engine(dsn)
    with engine.begin() as conn:
        for table in ("admin_sessions", "audit_events", "auth_rate_limits", "admin_users"):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("INSERT INTO admin_users (id,username,password_hash) VALUES (:id,:name,:hash)"), {"id":uuid.uuid4(),"name":"mock-admin","hash":hasher.hash(MOCK_PASSWORD)})
    yield engine
    engine.dispose()
    get_settings.cache_clear()


def anon(client):
    response=client.get('/api/v1/auth/csrf')
    assert response.status_code == 200
    return response.json()['data']['csrf_token']


def log_in(client, password=MOCK_PASSWORD, username='mock-admin'):
    token=anon(client)
    return client.post('/api/v1/auth/login',json={'username':username,'password':password},headers={'origin':ORIGIN,'x-csrf-token':token})


def test_session_cookie_logout_revocation_expiry_and_origin(pg):
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert client.get('/api/v1/private').status_code == 401
        csrf=anon(client)
        rejected=client.post('/api/v1/auth/login',json={'username':'mock-admin','password':MOCK_PASSWORD},headers={'origin':'https://evil.test','x-csrf-token':csrf})
        assert rejected.status_code == 403
        assert client.post('/api/v1/auth/login',json={'username':'mock-admin','password':MOCK_PASSWORD},headers={'origin':ORIGIN,'x-csrf-token':'bad'}).status_code == 403
        result=client.post('/api/v1/auth/login',json={'username':'mock-admin','password':MOCK_PASSWORD},headers={'origin':ORIGIN,'x-csrf-token':csrf})
        assert result.status_code == 200
        cookies=result.headers.get_list('set-cookie')
        session_cookie=next(c for c in cookies if c.startswith(SESSION_COOKIE+'='))
        assert all(x in session_cookie.lower() for x in ('secure','httponly','samesite=strict','path=/'))
        token=client.cookies.get(SESSION_COOKIE)
        session=client.get('/api/v1/auth/session')
        assert session.status_code == 200 and session.json()['data']['username']=='mock-admin'
        session_csrf=session.json()['data']['csrf_token']
        assert client.post('/api/v1/auth/logout',headers={'origin':'https://evil.test','x-csrf-token':session_csrf}).status_code == 403
        assert client.post('/api/v1/auth/logout',headers={'origin':ORIGIN,'x-csrf-token':session_csrf}).status_code == 204
        client.cookies.set(SESSION_COOKIE,token,domain='example.test',path='/')
        assert client.get('/api/v1/auth/session').status_code == 401
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code == 200
        with pg.begin() as conn: conn.execute(text("UPDATE admin_sessions SET expires_at=now()-interval '1 second' WHERE revoked_at IS NULL"))
        assert client.get('/api/v1/auth/session').status_code == 401


def test_rate_limit_shared_across_instances_and_uniform_error(pg):
    with TestClient(create_app(),base_url=ORIGIN) as first, TestClient(create_app(),base_url=ORIGIN) as second:
        for n in range(5):
            response=log_in(first if n%2==0 else second,password='wrong-mock-password',username='unknown' if n==0 else 'mock-admin')
            assert response.status_code == 401
            assert response.json()['error']['message']=='用户名或密码无效'
        response=log_in(second,password='wrong-mock-password')
        assert response.status_code == 429
        assert int(response.headers['retry-after']) > 0
        assert MOCK_PASSWORD not in response.text


def test_session_rotation_and_invalid_body_does_not_echo_password(pg):
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code == 200
        old=client.cookies.get(SESSION_COOKIE)
        assert log_in(client).status_code == 200
        assert client.cookies.get(SESSION_COOKIE) != old
        client.cookies.set(SESSION_COOKIE,old,domain='example.test',path='/')
        assert client.get('/api/v1/auth/session').status_code == 401
        token=anon(client)
        response=client.post('/api/v1/auth/login',content='{"username": 4, "password": "mock-secret-password"}',headers={'origin':ORIGIN,'x-csrf-token':token,'content-type':'application/json'})
        assert response.status_code == 422
        assert 'mock-secret-password' not in response.text


def test_init_admin_cannot_create_second(pg,monkeypatch,capsys):
    from backend import init_admin
    monkeypatch.setattr('sys.argv',['init_admin','--username','another'])
    monkeypatch.setattr(init_admin.getpass,'getpass',lambda _: MOCK_PASSWORD)
    assert init_admin.main() == 1
    assert '已存在' in capsys.readouterr().out
    with pg.connect() as conn: assert conn.execute(text('SELECT count(*) FROM admin_users')).scalar_one()==1


def test_init_admin_creates_argon2_hash_without_disclosing_password(pg, monkeypatch, capsys):
    from backend import init_admin
    with pg.begin() as conn:
        conn.execute(text('DELETE FROM admin_users'))
    monkeypatch.setattr('sys.argv', ['init_admin', '--username', 'new-admin'])
    monkeypatch.setattr(init_admin.getpass, 'getpass', lambda _: MOCK_PASSWORD)
    assert init_admin.main() == 0
    assert MOCK_PASSWORD not in capsys.readouterr().out
    with pg.connect() as conn:
        saved = conn.execute(text('SELECT password_hash FROM admin_users WHERE username=:name'), {'name': 'new-admin'}).scalar_one()
    assert saved != MOCK_PASSWORD and hasher.verify(saved, MOCK_PASSWORD)
