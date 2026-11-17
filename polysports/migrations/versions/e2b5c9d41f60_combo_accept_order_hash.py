"""Persist the taker order hash returned when accepting a combo quote."""
from alembic import op
import sqlalchemy as sa

revision = 'e2b5c9d41f60'
down_revision = 'd1a4f8c72e30'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('combo_rfqs', sa.Column('accept_order_hash', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('combo_rfqs', 'accept_order_hash')
