"""共享登录限流与安全审计。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_auth_controls"
down_revision = "0001_admin_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_rate_limits",
        sa.Column("bucket_key", sa.String(128), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("attempt_count >= 0", name="ck_auth_attempt_count"))
    op.create_index("ix_auth_rate_limits_updated_at", "auth_rate_limits", ["updated_at"])
    op.create_table("audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_users.id", ondelete="RESTRICT")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80)),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_audit_events_created_at", "audit_events", [sa.text("created_at DESC")])
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id", sa.text("created_at DESC")])


def downgrade():
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_auth_rate_limits_updated_at", table_name="auth_rate_limits")
    op.drop_table("auth_rate_limits")
