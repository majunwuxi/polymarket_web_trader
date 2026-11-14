"""将 NBA 同步计划切换到 Polymarket，保留历史赛事和订单。"""
from alembic import op

revision = 'd7a2e9b4c610'
down_revision = 'c4e391ad25b6'
branch_labels = None
depends_on = None


def upgrade():
    # 升级前必须停止 API 和 worker，避免旧租约继续写入。
    op.execute("""UPDATE job_runs SET result='abandoned',finished_at=now(),error_code='source_changed'
        WHERE result='running' AND job_id IN (SELECT j.id FROM jobs j JOIN sync_runs r ON r.id=j.sync_run_id
        WHERE r.sport='nba' AND r.provider='balldontlie' AND j.status IN ('pending','leased'))""")
    op.execute("""UPDATE sync_runs SET status='failed',finished_at=now(),error_code='source_changed',error_summary='NBA 赛事来源已调整'
        WHERE id IN (SELECT j.sync_run_id FROM jobs j JOIN sync_runs r ON r.id=j.sync_run_id
        WHERE r.sport='nba' AND r.provider='balldontlie' AND j.status IN ('pending','leased'))""")
    op.execute("""UPDATE jobs SET status='cancelled',lease_token=NULL,lease_owner=NULL,lease_until=NULL,updated_at=now()
        WHERE id IN (SELECT j.id FROM jobs j JOIN sync_runs r ON r.id=j.sync_run_id
        WHERE r.sport='nba' AND r.provider='balldontlie' AND j.status IN ('pending','leased'))""")
    op.execute("""INSERT INTO sync_schedules (id,provider,sport,utc_time,enabled)
        SELECT gen_random_uuid(),'polymarket','nba',s.utc_time,s.enabled FROM sync_schedules s
        WHERE s.sport='nba' AND s.provider='balldontlie'
        ON CONFLICT (provider,sport,utc_time) DO UPDATE SET enabled=EXCLUDED.enabled,updated_at=now()""")
    op.execute("UPDATE sync_schedules SET enabled=false,updated_at=now() WHERE sport='nba' AND provider='balldontlie'")
    op.execute('UPDATE schedule_revisions SET revision=revision+1 WHERE id=1')


def downgrade():
    op.execute("""UPDATE sync_schedules old SET enabled=new.enabled,updated_at=now() FROM sync_schedules new
        WHERE old.sport='nba' AND old.provider='balldontlie' AND new.sport='nba'
        AND new.provider='polymarket' AND old.utc_time=new.utc_time""")
    op.execute("UPDATE sync_schedules SET enabled=false,updated_at=now() WHERE sport='nba' AND provider='polymarket'")
    op.execute('UPDATE schedule_revisions SET revision=revision+1 WHERE id=1')
