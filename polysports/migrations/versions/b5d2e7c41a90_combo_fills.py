"""保存组合成交明细，并从官方签名报价回填历史已成交组合。

BUY 报价的 maker_amount 是链上签名接受订单实际支出的抵押品，taker_amount 是
实际收到的组合份额；历史库只保存了 blended_price、taker_amount 与余额要求
total_required。按 micro-USDC 精度重建 makerAmount 后即可给已 CONFIRMED 的组合
计算确定盈亏，不需要把预算或余额要求当成成交金额，也不需要猜测份额。

重建公式与 backend.combo_fills.rebuilt_cost 保持一致（此处内联，避免历史迁移
依赖当前应用代码）。只有签名报价字段完整且 total_required >= 重建成本时才回填。
"""
from alembic import op
import sqlalchemy as sa

from decimal import Decimal, InvalidOperation, ROUND_CEILING
import uuid

revision = 'b5d2e7c41a90'
down_revision = '39af194bb273'
branch_labels = None
depends_on = None

_MICRO = Decimal('0.000001')


def _rebuilt_cost(price, shares, required):
    try:
        p, s, r = Decimal(str(price)), Decimal(str(shares)), Decimal(str(required))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not all(x.is_finite() for x in (p, s, r)) or not (Decimal(0) < p < Decimal(1)) or s <= 0 or r <= 0:
        return None
    cost = (p * s).quantize(_MICRO, rounding=ROUND_CEILING)
    return cost if cost > 0 and cost <= r else None


def upgrade():
    op.create_table('combo_fills',
                    sa.Column('id', sa.UUID(), nullable=False),
                    sa.Column('rfq_id', sa.UUID(), nullable=False),
                    sa.Column('quote_id', sa.UUID(), nullable=True),
                    sa.Column('direction', sa.String(length=4), nullable=False),
                    sa.Column('cost', sa.Numeric(precision=38, scale=18), nullable=False),
                    sa.Column('size', sa.Numeric(precision=38, scale=18), nullable=False),
                    sa.Column('source', sa.String(length=24), nullable=False),
                    sa.Column('state', sa.String(length=16), nullable=False),
                    sa.Column('tx_hash', sa.String(length=255), nullable=True),
                    sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
                    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
                    sa.CheckConstraint("direction IN ('BUY','SELL')", name='ck_combo_fill_direction'),
                    sa.CheckConstraint("state IN ('pending','confirmed','void')", name='ck_combo_fill_state'),
                    sa.CheckConstraint("source IN ('signed_order','quote_rebuilt')", name='ck_combo_fill_source'),
                    sa.CheckConstraint('cost > 0 AND size > 0', name='ck_combo_fill_amounts'),
                    sa.ForeignKeyConstraint(['rfq_id'], ['combo_rfqs.id'], ondelete='RESTRICT'),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('rfq_id', name='uq_combo_fill_rfq'))
    op.create_index('ix_combo_fills_state', 'combo_fills', ['state'])

    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT id, quote_price, quote_receive, quote_required, tx_hash
        FROM combo_rfqs
        WHERE state = 'confirmed' AND direction = 'BUY'
          AND quote_price IS NOT NULL AND quote_receive IS NOT NULL AND quote_required IS NOT NULL
    """)).mappings().all()
    insert = sa.text("""
        INSERT INTO combo_fills
            (id, rfq_id, direction, cost, size, source, state, tx_hash, observed_at, confirmed_at)
        VALUES
            (:id, :rfq_id, 'BUY', :cost, :size, 'quote_rebuilt', 'confirmed', :tx_hash, now(), now())
    """)
    for row in rows:
        cost = _rebuilt_cost(row['quote_price'], row['quote_receive'], row['quote_required'])
        if cost is None:
            continue
        bind.execute(insert, {'id': uuid.uuid4(), 'rfq_id': row['id'],
                              'cost': cost, 'size': row['quote_receive'],
                              'tx_hash': row['tx_hash']})


def downgrade():
    op.drop_index('ix_combo_fills_state', table_name='combo_fills')
    op.drop_table('combo_fills')
