"""Allow the periodic combo position watchdog job."""
from alembic import op

revision = 'f4d7a1c93b20'
down_revision = 'e2b5c9d41f60'
branch_labels = None
depends_on = None

KINDS = "kind IN ('sync','result_poll','trade_reconcile','combo_reconcile','cleanup','combo_watchdog')"
OLD_KINDS = "kind IN ('sync','result_poll','trade_reconcile','combo_reconcile','cleanup')"
TARGETS = """(
    (kind = 'result_poll' AND watched_game_id IS NOT NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'sync' AND watched_game_id IS NULL AND sync_run_id IS NOT NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'trade_reconcile' AND trade_intent_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'combo_reconcile' AND combo_rfq_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL)
    OR (kind = 'cleanup' AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL AND schedule_id IS NULL)
    OR (kind = 'combo_watchdog' AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL AND schedule_id IS NULL)
)"""
OLD_TARGETS = """(
    (kind = 'result_poll' AND watched_game_id IS NOT NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'sync' AND watched_game_id IS NULL AND sync_run_id IS NOT NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'trade_reconcile' AND trade_intent_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND combo_rfq_id IS NULL)
    OR (kind = 'combo_reconcile' AND combo_rfq_id IS NOT NULL AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL)
    OR (kind = 'cleanup' AND watched_game_id IS NULL AND sync_run_id IS NULL AND trade_intent_id IS NULL AND combo_rfq_id IS NULL AND schedule_id IS NULL)
)"""


def upgrade():
    op.drop_constraint('ck_jobs_target', 'jobs', type_='check')
    op.drop_constraint('ck_jobs_kind', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_kind', 'jobs', KINDS)
    op.create_check_constraint('ck_jobs_target', 'jobs', TARGETS)


def downgrade():
    op.execute("DELETE FROM job_runs WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'combo_watchdog')")
    op.execute("DELETE FROM jobs WHERE kind = 'combo_watchdog'")
    op.drop_constraint('ck_jobs_target', 'jobs', type_='check')
    op.drop_constraint('ck_jobs_kind', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_kind', 'jobs', OLD_KINDS)
    op.create_check_constraint('ck_jobs_target', 'jobs', OLD_TARGETS)
