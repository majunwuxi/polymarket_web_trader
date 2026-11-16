"""Stop already-stale combo reconciliation jobs at the pending-check limit."""
from alembic import op

revision = 'b7c1e2f34a50'
down_revision = 'a8f3c1d92e40'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE jobs SET failure_count=120 WHERE kind='combo_reconcile' AND status='pending' AND attempt_count>=120")


def downgrade():
    pass
