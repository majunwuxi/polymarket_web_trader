"""保存官方组合报价的持仓标识；历史记录不猜测。"""
from alembic import op
import sqlalchemy as sa

revision = '39af194bb273'
down_revision = 'ed42c193a701'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('combo_rfqs', sa.Column('combo_position_id', sa.String(78), nullable=True))
    op.add_column('combo_rfqs', sa.Column('parent_combo_id', sa.UUID(), sa.ForeignKey('combo_rfqs.id', ondelete='RESTRICT'), nullable=True))
    op.add_column('trade_intents', sa.Column('parent_trade_id', sa.UUID(), sa.ForeignKey('trade_intents.id', ondelete='RESTRICT'), nullable=True))
    op.add_column('trade_intents', sa.Column('order_type', sa.String(3), nullable=False, server_default='GTC'))
    op.create_index('ix_trade_intents_parent_trade', 'trade_intents', ['parent_trade_id'])
    op.create_index('ix_combo_rfqs_parent_combo', 'combo_rfqs', ['parent_combo_id'])
    op.create_check_constraint('ck_trade_order_type', 'trade_intents', "(order_type='GTC' AND parent_trade_id IS NULL) OR (order_type='FOK' AND side='SELL' AND parent_trade_id IS NOT NULL)")
    op.create_check_constraint('ck_combo_close_direction', 'combo_rfqs', "parent_combo_id IS NULL OR direction='SELL'")


def downgrade():
    op.drop_constraint('ck_combo_close_direction', 'combo_rfqs', type_='check')
    op.drop_constraint('ck_trade_order_type', 'trade_intents', type_='check')
    op.drop_index('ix_combo_rfqs_parent_combo', table_name='combo_rfqs')
    op.drop_index('ix_trade_intents_parent_trade', table_name='trade_intents')
    op.drop_column('trade_intents', 'order_type')
    op.drop_column('trade_intents', 'parent_trade_id')
    op.drop_column('combo_rfqs', 'parent_combo_id')
    op.drop_column('combo_rfqs', 'combo_position_id')
