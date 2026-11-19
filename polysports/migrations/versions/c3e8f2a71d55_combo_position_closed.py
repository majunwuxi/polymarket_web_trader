"""Track combos whose on-chain position was closed outside this site."""
from alembic import op
import sqlalchemy as sa

revision = 'c3e8f2a71d55'
down_revision = 'f4d7a1c93b20'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('combo_rfqs', sa.Column('position_closed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('combo_rfqs', 'position_closed_at')
