from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as S
import uuid

from backend.scheduler import MAX_CONSECUTIVE_FAILURES, finish_attempt, retry_or_fail


def attempt():
    return S(result='running', finished_at=None, error_code=None, error_summary=None)


class Db:
    def __init__(self, row):
        self.row = row

    def scalar(self, query):
        return self.row


def job(failures=0):
    return S(id=uuid.uuid4(), attempt_count=1, lease_token=uuid.uuid4(), lease_owner='worker',
             lease_until=None, failure_count=failures, status='leased', due_at=None,
             last_error_code=None, last_error_summary=None, updated_at=None)


def test_retry_stops_after_consecutive_failures():
    now = datetime.now(timezone.utc)
    row = job()
    retry_or_fail(Db(attempt()), row, now, 'internal_error', timedelta(minutes=2))
    assert row.status == 'pending'
    assert row.failure_count == 1
    assert row.last_error_code == 'internal_error'
    assert row.due_at == now + timedelta(minutes=2)

    row.failure_count = MAX_CONSECUTIVE_FAILURES - 1
    row.status = 'leased'
    retry_or_fail(Db(attempt()), row, now, 'internal_error', timedelta(minutes=2))
    assert row.status == 'failed'
    assert row.failure_count == MAX_CONSECUTIVE_FAILURES
    assert row.last_error_code == 'retry_exhausted'
    assert '停止自动重试' in row.last_error_summary


def test_success_resets_failure_streak():
    row = job(failures=4)
    finish_attempt(Db(attempt()), row, datetime.now(timezone.utc), 'succeeded')
    assert row.failure_count == 0
    assert row.status == 'leased'
