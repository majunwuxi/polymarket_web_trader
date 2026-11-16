"""Persist whole-position redemption requests and transaction evidence."""
from alembic import op
import sqlalchemy as sa

revision = 'c7e3f9a12b40'
down_revision = 'b5d2e7c41a90'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('redeem_requests',
                    sa.Column('id', sa.UUID(), nullable=False),
                    sa.Column('wallet_id', sa.UUID(), nullable=False),
                    sa.Column('wallet_address', sa.String(length=42), nullable=False),
                    sa.Column('target_type', sa.String(length=16), nullable=False),
                    sa.Column('target_ref', sa.String(length=78), nullable=False),
                    sa.Column('intent_id', sa.UUID(), nullable=True),
                    sa.Column('combo_rfq_id', sa.UUID(), nullable=True),
                    sa.Column('condition_id', sa.String(length=66), nullable=True),
                    sa.Column('outcome_index', sa.Integer(), nullable=True),
                    sa.Column('position_id', sa.String(length=78), nullable=True),
                    sa.Column('shares', sa.Numeric(precision=38, scale=18), nullable=False),
                    sa.Column('client_request_id', sa.UUID(), nullable=False),
                    sa.Column('state', sa.String(length=24), nullable=False),
                    sa.Column('tx_hash', sa.String(length=66), nullable=True),
                    sa.Column('transaction_id', sa.String(length=128), nullable=True),
                    sa.Column('payout', sa.Numeric(precision=38, scale=18), nullable=True),
                    sa.Column('collateral_before', sa.Numeric(precision=38, scale=18), nullable=True),
                    sa.Column('collateral_balance', sa.Numeric(precision=38, scale=18), nullable=True),
                    sa.Column('error_code', sa.String(length=80), nullable=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
                    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
                    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
                    sa.CheckConstraint("target_type IN ('single','combo')", name='ck_redeem_target_type'),
                    sa.CheckConstraint("state IN ('submitting','confirmed','rejected','unknown')", name='ck_redeem_state'),
                    sa.CheckConstraint("wallet_address ~ '^0x[0-9a-fA-F]{40}$'", name='ck_redeem_wallet'),
                    sa.CheckConstraint("tx_hash IS NULL OR tx_hash ~ '^0x[0-9a-f]{64}$'", name='ck_redeem_tx_hash'),
                    sa.CheckConstraint("condition_id IS NULL OR condition_id ~ '^0x[0-9a-fA-F]{64}$'", name='ck_redeem_condition'),
                    sa.CheckConstraint('shares > 0', name='ck_redeem_shares'),
                    sa.ForeignKeyConstraint(['intent_id'], ['trade_intents.id'], ondelete='RESTRICT'),
                    sa.ForeignKeyConstraint(['combo_rfq_id'], ['combo_rfqs.id'], ondelete='RESTRICT'),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('wallet_id', 'target_type', 'target_ref', name='uq_redeem_target'),
                    sa.UniqueConstraint('tx_hash', name='uq_redeem_tx'))
    op.create_index('ix_redeem_state_created', 'redeem_requests', ['state', sa.text('created_at DESC')])
    op.create_index('ix_redeem_intent', 'redeem_requests', ['intent_id'])
    op.create_index('ix_redeem_combo', 'redeem_requests', ['combo_rfq_id'])


def downgrade():
    op.drop_table('redeem_requests')
