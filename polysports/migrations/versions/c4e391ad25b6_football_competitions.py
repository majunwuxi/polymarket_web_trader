"""收窄足球列表，保留既有赛事和交易历史。"""
from alembic import op

revision = 'c4e391ad25b6'
down_revision = 'bc2e79a1d075'
branch_labels = None
depends_on = None


def upgrade():
    # 旧数据没有可靠赛事标签；仅去除未来列表可见性，等待完整同步确认。
    op.execute("UPDATE games SET market_available=false WHERE provider='polymarket' AND sport='football'")


def downgrade():
    # 不推测历史比赛此前是否可用；下次完整同步重新确定。
    pass
