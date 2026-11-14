"""记录已确认订单需要绑定的外部赛果来源。"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision='f47b10cd325e'
down_revision='d7a2e9b4c610'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('result_binding_requests',
        sa.Column('id',UUID(as_uuid=True),primary_key=True),
        sa.Column('game_id',UUID(as_uuid=True),sa.ForeignKey('games.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('provider',sa.String(32),nullable=False),
        sa.Column('state',sa.String(20),nullable=False,server_default='pending'),
        sa.Column('candidates',JSONB,nullable=False,server_default=sa.text("'[]'::jsonb")),
        sa.Column('fixture_id',sa.String(255),nullable=True),
        sa.Column('last_error_code',sa.String(80),nullable=True),
        sa.Column('next_attempt_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('game_id',name='uq_result_binding_game'),
        sa.UniqueConstraint('provider','fixture_id',name='uq_result_binding_fixture'),
        sa.CheckConstraint("provider IN ('api_football','balldontlie')",name='ck_result_binding_provider'),
        sa.CheckConstraint("state IN ('pending','bound','needs_review')",name='ck_result_binding_state'))


def downgrade():
    op.drop_table('result_binding_requests')
