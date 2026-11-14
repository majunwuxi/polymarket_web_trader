"""区分历史 Builder 请求与个人 Requester 请求。"""
from alembic import op
import sqlalchemy as sa

revision = 'ed42c193a701'
down_revision = 'f47b10cd325e'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('combo_rfqs', sa.Column('gateway_mode', sa.String(16), nullable=False, server_default='builder'))


def downgrade():
    op.drop_column('combo_rfqs', 'gateway_mode')
