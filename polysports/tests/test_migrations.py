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
    monkeypatch.setenv("POLYSPORTS_AUTH_SECRET", "mock-only-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setenv("POLYSPORTS_PUBLIC_ORIGIN", "https://example.test")
    from backend.config import get_settings
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    engine = create_engine(dsn)
    try:
        # 避免对已有数据做破坏性迁移；调用者必须创建全新隔离数据库。
        if inspect(engine).get_table_names():
            pytest.fail("迁移测试数据库必须为空")
        command.upgrade(cfg, "64099dce6480")
        old_run,old_job,old_attempt,token=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO sync_runs(id,provider,sport,trigger_type,scheduled_for,started_at,window_start,window_end,status) VALUES (:id,'api_football','football','manual',now(),now(),now(),now()+interval '36 hours','running')"),{'id':old_run})
            conn.execute(text("INSERT INTO jobs(id,kind,dedupe_key,sync_run_id,due_at,status,lease_owner,lease_until,lease_token,attempt_count) VALUES (:id,'sync','mock-old-sync',:run,now(),'leased','mock-worker',now()+interval '2 minutes',:token,1)"),{'id':old_job,'run':old_run,'token':token})
            conn.execute(text("INSERT INTO job_runs(id,job_id,attempt_no,lease_token,started_at,result) VALUES (:id,:job,1,:token,now(),'running')"),{'id':old_attempt,'job':old_job,'token':token})
        command.upgrade(cfg, "bc2e79a1d075")
        old_home,old_away,old_game=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO teams(id,sport,source_name) VALUES (:home,'football','Mock Home'),(:away,'football','Mock Away')"),{'home':old_home,'away':old_away})
            conn.execute(text("INSERT INTO games(id,provider,external_id,sport,home_team_id,away_team_id,starts_at,status,market_available) VALUES (:id,'polymarket','mock-old','football',:home,:away,now(),'scheduled',true)"),{'id':old_game,'home':old_home,'away':old_away})
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM sync_schedules WHERE provider='polymarket' AND enabled")).scalar_one()==2
            assert conn.execute(text("SELECT count(*) FROM sync_schedules WHERE provider='api_football' AND enabled")).scalar_one()==0
            assert conn.execute(text('SELECT status FROM jobs WHERE id=:id'),{'id':old_job}).scalar_one()=='cancelled'
            assert conn.execute(text('SELECT result FROM job_runs WHERE id=:id'),{'id':old_attempt}).scalar_one()=='abandoned'
            assert conn.execute(text('SELECT error_code FROM sync_runs WHERE id=:id'),{'id':old_run}).scalar_one()=='source_changed'
            assert conn.execute(text('SELECT market_available FROM games WHERE id=:id'),{'id':old_game}).scalar_one() is False
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
