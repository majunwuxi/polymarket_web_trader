"""切换足球同步计划，保留旧赛事和同步历史。"""
from alembic import op
import sqlalchemy as sa

revision = 'bc2e79a1d075'
down_revision = '64099dce6480'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('games',sa.Column('market_available',sa.Boolean(),nullable=True))
    # 升级时服务必须停止；中断未完成的旧足球同步，避免旧租约重启后写入。
    op.execute("""UPDATE job_runs SET result='abandoned', finished_at=now(), error_code='source_changed'
        WHERE result='running' AND job_id IN (
            SELECT j.id FROM jobs j JOIN sync_runs r ON r.id=j.sync_run_id
            WHERE r.sport='football' AND r.provider='api_football' AND j.status IN ('pending','leased'))""")
    op.execute("""UPDATE sync_runs SET status='failed', finished_at=now(), error_code='source_changed',
        error_summary='足球赛事来源已调整' WHERE id IN (
            SELECT j.sync_run_id FROM jobs j JOIN sync_runs r ON r.id=j.sync_run_id
            WHERE r.sport='football' AND r.provider='api_football' AND j.status IN ('pending','leased'))""")
    op.execute("""UPDATE jobs SET status='cancelled', lease_token=NULL, lease_owner=NULL, lease_until=NULL, updated_at=now()
        WHERE id IN (SELECT j.id FROM jobs j JOIN sync_runs r ON r.id=j.sync_run_id
        WHERE r.sport='football' AND r.provider='api_football' AND j.status IN ('pending','leased'))""")
    # 新计划使用新 ID，避免旧计划已有的时间点阻止新来源创建任务。
    op.execute("""INSERT INTO sync_schedules (id,provider,sport,utc_time,enabled)
        SELECT gen_random_uuid(),'polymarket','football',s.utc_time,s.enabled FROM sync_schedules s
        WHERE s.sport='football' AND s.provider='api_football'
        ON CONFLICT (provider,sport,utc_time) DO UPDATE SET enabled=EXCLUDED.enabled,updated_at=now()""")
    op.execute("UPDATE sync_schedules SET enabled=false, updated_at=now() WHERE sport='football' AND provider='api_football'")
    op.execute('UPDATE schedule_revisions SET revision=revision+1 WHERE id=1')


def downgrade():
    op.execute("""UPDATE sync_schedules old SET enabled=new.enabled,updated_at=now() FROM sync_schedules new
        WHERE old.sport='football' AND old.provider='api_football' AND new.sport='football'
        AND new.provider='polymarket' AND old.utc_time=new.utc_time""")
    op.execute("UPDATE sync_schedules SET enabled=false,updated_at=now() WHERE sport='football' AND provider='polymarket'")
    op.execute('UPDATE schedule_revisions SET revision=revision+1 WHERE id=1')
    op.drop_column('games','market_available')
