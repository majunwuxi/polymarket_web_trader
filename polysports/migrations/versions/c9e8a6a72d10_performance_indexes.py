"""Add high-value indexes for list, history, and background polling queries."""
from alembic import op
import sqlalchemy as sa

revision = 'c9e8a6a72d10'
down_revision = 'b7c1e2f34a50'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_combo_rfqs_created_id', 'combo_rfqs', ['created_at', 'id'])
    op.create_index('ix_combo_rfqs_state_created', 'combo_rfqs', ['state', 'created_at', 'id'])
    op.create_index('ix_trade_intents_game_id', 'trade_intents', ['game_id'])
    op.create_index('ix_combo_legs_game_id', 'combo_legs', ['game_id'])
    op.create_index('ix_jobs_due_id', 'jobs', ['due_at', 'id'])
    op.create_index('ix_result_binding_state_next', 'result_binding_requests', ['state', 'next_attempt_at'])


def downgrade():
    op.drop_index('ix_result_binding_state_next', table_name='result_binding_requests')
    op.drop_index('ix_jobs_due_id', table_name='jobs')
    op.drop_index('ix_combo_legs_game_id', table_name='combo_legs')
    op.drop_index('ix_trade_intents_game_id', table_name='trade_intents')
    op.drop_index('ix_combo_rfqs_state_created', table_name='combo_rfqs')
    op.drop_index('ix_combo_rfqs_created_id', table_name='combo_rfqs')
