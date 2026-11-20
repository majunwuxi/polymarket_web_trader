"""Stop listing second-division football competitions (英冠/德乙/法乙/荷乙)."""
from alembic import op

revision = 'b9d4e6c02f18'
down_revision = 'c3e8f2a71d55'
branch_labels = None
depends_on = None


def upgrade():
    # 白名单已移除乙级联赛；先隐藏既有比赛，等待下次完整同步按新白名单重新确认。
    op.execute("UPDATE games SET market_available=false WHERE provider='polymarket' AND sport='football'")


def downgrade():
    # 不推测历史比赛此前是否可用；下次完整同步重新确定。
    pass
