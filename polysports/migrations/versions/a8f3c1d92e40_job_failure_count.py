"""Track consecutive job failures for bounded reconciliation retries."""
from alembic import op
import sqlalchemy as sa

revision = 'a8f3c1d92e40'
down_revision = 'c7e3f9a12b40'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('failure_count', sa.Integer(), server_default='0', nullable=False))
    op.execute("UPDATE jobs SET failure_count=10 WHERE kind IN ('trade_reconcile','combo_reconcile') AND status='pending' AND last_error_code='internal_error'")


def downgrade():
    op.drop_column('jobs', 'failure_count')
