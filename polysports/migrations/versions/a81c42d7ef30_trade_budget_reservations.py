import sqlalchemy as sa
from alembic import op

revision = 'a81c42d7ef30'
down_revision = 'e6a2f8d41c73'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'trade_limit_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_limit_percent', sa.Numeric(), nullable=True),
        sa.Column('order_limit_floor_usd', sa.Numeric(), nullable=False),
        sa.Column('max_daily_usd', sa.Numeric(), nullable=True),
        sa.CheckConstraint('id = 1', name='ck_trade_limit_settings_singleton'),
    )
    op.create_table(
        'trade_budget_reservations',
        sa.Column('order_id', sa.UUID(), primary_key=True),
        sa.Column('wallet_id', sa.UUID(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_trade_budget_positive'),
    )
    op.create_index('ix_trade_budget_wallet_day', 'trade_budget_reservations', ['wallet_id', 'day'])


def downgrade():
    op.drop_table('trade_budget_reservations')
    op.drop_table('trade_limit_settings')
