import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'd53ab7c89120'
down_revision = 'a81c42d7ef30'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'football_display_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('competition_ids', postgresql.JSONB(), nullable=True),
        sa.CheckConstraint('id = 1', name='ck_football_display_singleton'),
    )


def downgrade():
    op.drop_table('football_display_settings')
