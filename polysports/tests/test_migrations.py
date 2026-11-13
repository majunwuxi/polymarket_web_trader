"""仅允许在名称以 polysports_test_ 开头的 loopback 临时数据库运行。"""
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def test_isolated_postgres_upgrade_downgrade_upgrade(monkeypatch):
    dsn = os.environ.get("POLYSPORTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("未提供隔离的 POLYSPORTS_TEST_DATABASE_URL，未执行 PostgreSQL 迁移测试")
    url = make_url(dsn)
    if url.drivername != "postgresql+psycopg" or url.host not in ("127.0.0.1", "localhost", "::1") or not (url.database or "").startswith("polysports_test_"):
        pytest.fail("迁移测试只允许本地 polysports_test_ 前缀的隔离数据库")
    monkeypatch.setenv("POLYSPORTS_DATABASE_URL", dsn)
    from backend.config import get_settings
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    engine = create_engine(dsn)
    try:
        # 避免对已有数据做破坏性迁移；调用者必须创建全新隔离数据库。
        if inspect(engine).get_table_names():
            pytest.fail("迁移测试数据库必须为空")
        command.upgrade(cfg, "head")
        assert {"admin_users", "admin_sessions", "alembic_version"} <= set(inspect(engine).get_table_names())
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO admin_users (id, username, password_hash) VALUES (:id, :name, :pw)"), {"id": uuid.uuid4(), "name": "mock_admin", "pw": "mock_hash"})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO admin_users (id, username, password_hash) VALUES (:id, :name, :pw)"), {"id": uuid.uuid4(), "name": "mock_other", "pw": "mock_hash"})
        command.downgrade(cfg, "base")
        assert "admin_users" not in inspect(engine).get_table_names()
        command.upgrade(cfg, "head")
        assert "admin_sessions" in inspect(engine).get_table_names()
        command.check(cfg)
        assert inspect(engine).get_foreign_keys("admin_sessions")[0]["referred_table"] == "admin_users"
        assert any(i["name"] == "ix_admin_sessions_admin_expiry" for i in inspect(engine).get_indexes("admin_sessions"))
    finally:
        engine.dispose()
        get_settings.cache_clear()
