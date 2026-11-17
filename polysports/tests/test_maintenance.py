from types import SimpleNamespace

from backend import maintenance


class FakeDb:
    def __init__(self):
        self.calls=[]
        self.counts=iter([2,3,4])

    def execute(self, statement, params):
        self.calls.append((str(statement),params))
        return SimpleNamespace(rowcount=next(self.counts))


def test_cleanup_expired_only_deletes_safe_rows():
    db=FakeDb()
    counts=maintenance.cleanup_expired(db)
    assert counts=={'admin_sessions':2,'auth_rate_limits':3,'audit_events':4}
    sessions,limits,audit=db.calls
    assert 'expires_at < now()' in sessions[0] and 'revoked_at IS NOT NULL' in sessions[0]
    assert sessions[1]=={'days':maintenance.SESSION_RETENTION_DAYS}
    assert 'blocked_until IS NULL OR blocked_until <= now()' in limits[0]
    assert limits[1]=={'hours':maintenance.RATE_LIMIT_RETENTION_HOURS}
    assert 'created_at < now()' in audit[0]
    assert audit[1]=={'days':maintenance.AUDIT_RETENTION_DAYS}
    assert maintenance.SESSION_RETENTION_DAYS==1
    assert maintenance.RATE_LIMIT_RETENTION_HOURS==24
    assert maintenance.AUDIT_RETENTION_DAYS==30
