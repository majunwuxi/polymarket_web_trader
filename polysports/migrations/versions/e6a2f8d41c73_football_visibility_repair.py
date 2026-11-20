"""Restore visibility for whitelisted football competitions after the blanket hide."""
from alembic import op

revision = 'e6a2f8d41c73'
down_revision = 'b9d4e6c02f18'
branch_labels = None
depends_on = None

# 发现白名单中的竞赛标签（与 backend/polymarket_football.py 保持一致）。
WHITELIST = (
    '306', '1494', '100618', '780', '102070', '101735', '101772', '104930',
    '102651', '105243', '1234', '101787', '102763', '104937', '102232',
    '102544', '102540', '101680', '101280', '100787', '104327', '104342',
    '104316', '100816', '102648',
)
REMOVED = ('102643', '102864', '102871', '105727')


def _ids(values):
    return ', '.join(f"'{value}'" for value in values)


def upgrade():
    # b9d4e6c02f18 把全部 Polymarket 足球比赛置为隐藏，只能等下一次定时同步恢复；
    # 联赛关联（leagues.external_id = 竞赛标签）已可靠，这里按联赛精确恢复：
    # 白名单联赛立即重新可见，被移除的乙级联赛保持隐藏，其余保持不变。
    op.execute(f"""UPDATE games g SET market_available = true FROM leagues l
        WHERE g.league_id = l.id AND g.provider = 'polymarket' AND g.sport = 'football'
          AND l.provider = 'polymarket' AND l.external_id IN ({_ids(WHITELIST)})""")
    op.execute(f"""UPDATE games g SET market_available = false FROM leagues l
        WHERE g.league_id = l.id AND g.provider = 'polymarket' AND g.sport = 'football'
          AND l.provider = 'polymarket' AND l.external_id IN ({_ids(REMOVED)})""")


def downgrade():
    # 恢复可见性属于数据修复，回滚不重新隐藏；下一次完整同步按当时白名单重新确认。
    pass
