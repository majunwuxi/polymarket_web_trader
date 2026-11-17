"""Allow daily retention cleanup jobs."""
from alembic import op

revision = 'd1a4f8c72e30'
down_revision = 'c9e8a6a72d10'
branch_labels = None
depends_on = None

KINDS = "kind IN ('sync','result_poll','trade_reconcile','combo_reconcile','cleanup')"
OLD_KINDS = "kind IN ('sync','result_poll','trade_reconcile','combo_reconcile')"
TARGETS = """(
    (kind = 'result_poll' AND watched_game_id IS NOT NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'sync' AND watched_game_id IS NULL AND sync_run_id IS NOT NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'trade_reconcile' AND trade_intent_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'combo_reconcile' AND combo_rfq_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL)
    OR (kind = 'cleanup' AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL AND schedule_id IS NULL)
)"""
OLD_TARGETS = """(
    (kind = 'result_poll' AND watched_game_id IS NOT NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'sync' AND watched_game_id IS NULL AND sync_run_id IS NOT NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'trade_reconcile' AND trade_intent_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'combo_reconcile' AND combo_rfq_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL)
)"""


def upgrade():
    op.drop_constraint('ck_jobs_target', 'jobs', type_='check')
    op.drop_constraint('ck_jobs_kind', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_kind', 'jobs', KINDS)
    op.create_check_constraint('ck_jobs_target', 'jobs', TARGETS)


def downgrade():
    op.execute("DELETE FROM job_runs WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'cleanup')")
    op.execute("DELETE FROM jobs WHERE kind = 'cleanup'")
    op.drop_constraint('ck_jobs_target', 'jobs', type_='check')
    op.drop_constraint('ck_jobs_kind', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_kind', 'jobs', OLD_KINDS)
    op.create_check_constraint('ck_jobs_target', 'jobs', OLD_TARGETS)
