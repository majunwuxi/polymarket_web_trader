"""管理员和会话基础表；认证逻辑将在 6B 实现。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_admin_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("singleton", name="ck_admin_singleton_true"),
        sa.UniqueConstraint("singleton", name="uq_admin_singleton"),
        sa.UniqueConstraint("username", name="uq_admin_username"))
    op.create_table("admin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("csrf_secret_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_session_token_hash"))
    op.create_index("ix_admin_sessions_admin_expiry", "admin_sessions", ["admin_id", "expires_at"])


def downgrade():
    op.drop_index("ix_admin_sessions_admin_expiry", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("admin_users")
