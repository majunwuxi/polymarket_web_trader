"""模拟账户和密码；仅在隔离的本机 PostgreSQL 测试库运行。"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.auth import LOGIN_COOKIE, SESSION_COOKIE, hasher
from backend.config import get_settings
from backend.main import create_app

MOCK_PASSWORD = "mock-password-12345"
MOCK_SECRET = "mock-only-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
ORIGIN = "https://example.test"

@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("POLYSPORTS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("未提供隔离 PostgreSQL；未执行认证集成测试")
    url = make_url(dsn)
    if url.drivername != "postgresql+psycopg" or url.host not in ("127.0.0.1", "localhost", "::1") or not (url.database or "").startswith("polysports_test_"):
        pytest.fail("认证测试仅允许本机隔离测试数据库")
    monkeypatch.setenv("POLYSPORTS_DATABASE_URL", dsn)
    monkeypatch.setenv("POLYSPORTS_AUTH_SECRET", MOCK_SECRET)
    monkeypatch.setenv("POLYSPORTS_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("POLYSPORTS_ENVIRONMENT", "test")
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    engine=create_engine(dsn)
    with engine.begin() as conn:
        conn.execute(text("UPDATE market_links SET confirmed_by=NULL WHERE confirmed_by IS NOT NULL"))
        for table in ("admin_sessions", "audit_events", "auth_rate_limits", "admin_users"):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("INSERT INTO admin_users (id,username,password_hash) VALUES (:id,:name,:hash)"), {"id":uuid.uuid4(),"name":"mock-admin","hash":hasher.hash(MOCK_PASSWORD)})
    yield engine
    engine.dispose()
    get_settings.cache_clear()


def anon(client):
    response=client.get('/api/v1/auth/csrf')
    assert response.status_code == 200
    return response.json()['data']['csrf_token']


def log_in(client, password=MOCK_PASSWORD, username='mock-admin'):
    token=anon(client)
    return client.post('/api/v1/auth/login',json={'username':username,'password':password},headers={'origin':ORIGIN,'x-csrf-token':token})


def test_session_cookie_logout_revocation_expiry_and_origin(pg):
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert client.get('/api/v1/private').status_code == 401
        csrf=anon(client)
        rejected=client.post('/api/v1/auth/login',json={'username':'mock-admin','password':MOCK_PASSWORD},headers={'origin':'https://evil.test','x-csrf-token':csrf})
        assert rejected.status_code == 403
        assert client.post('/api/v1/auth/login',json={'username':'mock-admin','password':MOCK_PASSWORD},headers={'origin':ORIGIN,'x-csrf-token':'bad'}).status_code == 403
        result=client.post('/api/v1/auth/login',json={'username':'mock-admin','password':MOCK_PASSWORD},headers={'origin':ORIGIN,'x-csrf-token':csrf})
        assert result.status_code == 200
        cookies=result.headers.get_list('set-cookie')
        session_cookie=next(c for c in cookies if c.startswith(SESSION_COOKIE+'='))
        assert all(x in session_cookie.lower() for x in ('secure','httponly','samesite=strict','path=/'))
        token=client.cookies.get(SESSION_COOKIE)
        session=client.get('/api/v1/auth/session')
        assert session.status_code == 200 and session.json()['data']['username']=='mock-admin'
        session_csrf=session.json()['data']['csrf_token']
        assert client.post('/api/v1/auth/logout',headers={'origin':'https://evil.test','x-csrf-token':session_csrf}).status_code == 403
        assert client.post('/api/v1/auth/logout',headers={'origin':ORIGIN,'x-csrf-token':session_csrf}).status_code == 204
        client.cookies.set(SESSION_COOKIE,token,domain='example.test',path='/')
        assert client.get('/api/v1/auth/session').status_code == 401
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code == 200
        with pg.begin() as conn: conn.execute(text("UPDATE admin_sessions SET expires_at=now()-interval '1 second' WHERE revoked_at IS NULL"))
        assert client.get('/api/v1/auth/session').status_code == 401


def test_rate_limit_shared_across_instances_and_uniform_error(pg):
    with TestClient(create_app(),base_url=ORIGIN) as first, TestClient(create_app(),base_url=ORIGIN) as second:
        for n in range(5):
            response=log_in(first if n%2==0 else second,password='wrong-mock-password',username='unknown' if n==0 else 'mock-admin')
            assert response.status_code == 401
            assert response.json()['error']['message']=='用户名或密码无效'
        response=log_in(second,password='wrong-mock-password')
        assert response.status_code == 429
        assert int(response.headers['retry-after']) > 0
        assert MOCK_PASSWORD not in response.text


def test_session_rotation_and_invalid_body_does_not_echo_password(pg):
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code == 200
        old=client.cookies.get(SESSION_COOKIE)
        assert log_in(client).status_code == 200
        assert client.cookies.get(SESSION_COOKIE) != old
        client.cookies.set(SESSION_COOKIE,old,domain='example.test',path='/')
        assert client.get('/api/v1/auth/session').status_code == 401
        token=anon(client)
        response=client.post('/api/v1/auth/login',content='{"username": 4, "password": "mock-secret-password"}',headers={'origin':ORIGIN,'x-csrf-token':token,'content-type':'application/json'})
        assert response.status_code == 422
        assert 'mock-secret-password' not in response.text


def test_init_admin_cannot_create_second(pg,monkeypatch,capsys):
    from backend import init_admin
    monkeypatch.setattr('sys.argv',['init_admin','--username','another'])
    monkeypatch.setattr(init_admin.getpass,'getpass',lambda _: MOCK_PASSWORD)
    assert init_admin.main() == 1
    assert '已存在' in capsys.readouterr().out
    with pg.connect() as conn: assert conn.execute(text('SELECT count(*) FROM admin_users')).scalar_one()==1


def test_init_admin_creates_argon2_hash_without_disclosing_password(pg, monkeypatch, capsys):
    from backend import init_admin
    with pg.begin() as conn:
        conn.execute(text('DELETE FROM admin_users'))
    monkeypatch.setattr('sys.argv', ['init_admin', '--username', 'new-admin'])
    monkeypatch.setattr(init_admin.getpass, 'getpass', lambda _: MOCK_PASSWORD)
    assert init_admin.main() == 0
    assert MOCK_PASSWORD not in capsys.readouterr().out
    with pg.connect() as conn:
        saved = conn.execute(text('SELECT password_hash FROM admin_users WHERE username=:name'), {'name': 'new-admin'}).scalar_one()
    assert saved != MOCK_PASSWORD and hasher.verify(saved, MOCK_PASSWORD)


def test_sports_sync_idempotency_history_concurrency_and_api(pg):
    from datetime import datetime, timezone
    import httpx
    from backend.sports_sync import run_sync
    from test_sports_sources import mock_football
    start=datetime(2026,1,1,12,tzinfo=timezone.utc)
    def response(row):
        return httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'errors':[],'paging':{'current':1,'total':1},'response':[row]})))
    with pg.begin() as conn:
        for table in ('game_revisions','games','team_aliases','team_source_ids','teams','leagues','sync_runs'):
            conn.execute(text(f'DELETE FROM {table}'))
    row=mock_football()
    def execute():
        with response(row) as client: return run_sync(pg,'api_football','mock-key',client=client,now=start)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: execute(), range(2)))
    with pg.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM games')).scalar_one()==1
        assert conn.execute(text('SELECT count(*) FROM teams')).scalar_one()==2
        assert conn.execute(text('SELECT count(*) FROM game_revisions')).scalar_one()==0
    row=mock_football(when='2026-01-01T14:00:00Z',status='PST')
    execute()
    with pg.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM games')).scalar_one()==1
        assert conn.execute(text('SELECT count(*) FROM game_revisions')).scalar_one()==1
        assert conn.execute(text('SELECT status FROM games')).scalar_one()=='postponed'
    row=mock_football(when='2026-01-01T14:00:00Z',status='CANC')
    row['goals']={'home':2,'away':1}
    execute()
    with pg.connect() as conn:
        assert conn.execute(text('SELECT status FROM games')).scalar_one()=='cancelled'
        assert conn.execute(text('SELECT count(*) FROM game_revisions')).scalar_one()==2
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429))) as client:
        failed=run_sync(pg,'api_football','mock-secret',client=client,now=start)
    with pg.connect() as conn:
        assert conn.execute(text('SELECT status FROM sync_runs WHERE id=:id'),{'id':failed}).scalar_one()=='failed'
        assert conn.execute(text('SELECT count(*) FROM games')).scalar_one()==1
    row=mock_football(ident=2,when='2026-01-01T15:00:00Z')
    execute()
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code==200
        session=client.get('/api/v1/auth/session').json()['data']
        headers={'origin':ORIGIN,'x-csrf-token':session['csrf_token']}
        result=client.get('/api/v1/games',params={'sport':'football','from':'2026-01-01T00:00:00Z','to':'2026-01-03T00:00:00Z','limit':1})
        assert result.status_code==200
        page=result.json()['data']
        game=page['items'][0]
        assert page['next_cursor']
        second=client.get('/api/v1/games',params={'sport':'football','from':'2026-01-01T00:00:00Z','to':'2026-01-03T00:00:00Z','limit':1,'cursor':page['next_cursor']}).json()['data']
        assert len(second['items'])==1 and second['items'][0]['id']!=game['id'] and second['next_cursor'] is None
        assert game['home_team']['name_zh'] is None
        assert len(client.get('/api/v1/games/'+game['id']).json()['data']['recent_revisions'])==2
        team_id=game['home_team']['id']
        assert client.patch('/api/v1/teams/'+team_id,json={'name_zh':'模拟球队','translation_status':'verified'},headers=headers).status_code==200
        alias={'alias':'模拟别名','language':'zh-CN','verified':True}
        assert client.post('/api/v1/teams/'+team_id+'/aliases',json=alias,headers=headers).status_code==201
        assert client.post('/api/v1/teams/'+team_id+'/aliases',json=alias,headers=headers).status_code==409
        assert client.get('/api/v1/sync-runs').json()['data']['items'][0]['status'] in ('succeeded','failed')


def setup_scheduler_game(pg, sport='football'):
    from datetime import datetime, timedelta, timezone
    import httpx
    from backend.sports_sync import run_sync
    from test_sports_sources import mock_football, mock_nba
    with pg.begin() as conn:
        for table in ('job_runs','jobs','watched_games','result_binding_requests','game_revisions','games','team_aliases','team_source_ids','teams','leagues','sync_runs'):
            conn.execute(text(f'DELETE FROM {table}'))
    now=datetime.now(timezone.utc)
    if sport=='football':
        row=mock_football(when=(now+timedelta(hours=1)).isoformat())
        def handler(req): return httpx.Response(200,json={'errors':[],'paging':{'current':1,'total':1},'response':[row]})
        provider='api_football'
    else:
        row=mock_nba(when=(now+timedelta(hours=1)).isoformat())
        def handler(req): return httpx.Response(200,json={'data':[] if req.url.params.get('season_type') else [row],'meta':{}})
        provider='balldontlie'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client: run_sync(pg,provider,'mock-key',client=client,now=now)
    with pg.connect() as conn: game_id=conn.execute(text('SELECT id FROM games')).scalar_one()
    return game_id,row


def test_scheduler_watch_due_concurrency_expiry_and_finish(pg):
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from backend.scheduler import watch_game, claim_next, renew
    from backend.worker import apply_work
    from backend.sports_sources import football_game
    game_id,row=setup_scheduler_game(pg)
    with Session(pg) as db, db.begin():
        watch=watch_game(db,game_id); watch_id=watch.id
        assert watch_game(db,game_id).id==watch_id
    with pg.connect() as conn:
        due,start=conn.execute(text('SELECT j.due_at,g.starts_at FROM jobs j JOIN watched_games w ON w.id=j.watched_game_id JOIN games g ON g.id=w.game_id')).one()
        assert due-start==timedelta(minutes=120)
    assert claim_next(pg,'mock-worker') is None
    with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second'"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims=list(executor.map(lambda n:claim_next(pg,f'mock-{n}'),range(2)))
    assert sum(x is not None for x in claims)==1
    job_id,old_token=next(x for x in claims if x)
    assert renew(pg,job_id,old_token,120)
    with pg.begin() as conn: conn.execute(text("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"),{'id':job_id})
    assert apply_work(pg,job_id,old_token,football_game(row)) is False
    new_id,new_token=claim_next(pg,'mock-restarted-worker')
    assert new_id==job_id and new_token!=old_token
    assert not renew(pg,job_id,old_token,120)
    row['fixture']['status']['short']='FT'; row['goals']={'home':2,'away':1}
    assert apply_work(pg,job_id,new_token,football_game(row)) is True
    with pg.connect() as conn:
        assert conn.execute(text('SELECT state FROM watched_games WHERE id=:id'),{'id':watch_id}).scalar_one()=='completed'
        assert conn.execute(text('SELECT status FROM jobs WHERE id=:id'),{'id':job_id}).scalar_one()=='done'
        assert conn.execute(text('SELECT array_agg(result ORDER BY attempt_no) FROM job_runs WHERE job_id=:id'),{'id':job_id}).scalar_one()==['abandoned','succeeded']
    assert claim_next(pg,'mock-worker') is None


def test_scheduler_nba_retry_and_first_due(pg):
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from backend.scheduler import watch_game, claim_next
    from backend.worker import apply_work
    game_id,_=setup_scheduler_game(pg,'nba')
    with Session(pg) as db, db.begin(): watch_game(db,game_id)
    with pg.connect() as conn:
        due,start=conn.execute(text('SELECT j.due_at,g.starts_at FROM jobs j JOIN watched_games w ON w.id=j.watched_game_id JOIN games g ON g.id=w.game_id')).one()
        assert due-start==timedelta(minutes=159)
    with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second'"))
    job_id,token=claim_next(pg,'mock-worker')
    assert apply_work(pg,job_id,token,error='http_429')
    with pg.connect() as conn:
        state,due,now=conn.execute(text('SELECT w.state,j.due_at,clock_timestamp() FROM jobs j JOIN watched_games w ON j.watched_game_id=w.id')).one()
        assert state=='active' and due>now+timedelta(minutes=2,seconds=55)
        assert conn.execute(text('SELECT error_code FROM job_runs')).scalar_one()=='http_429'


def test_scheduler_reschedules_unchecked_event_and_api_revision(pg):
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from backend.scheduler import watch_game, claim_next
    from backend.sports_sync import upsert_game
    from backend.sports_sources import football_game
    game_id,row=setup_scheduler_game(pg)
    with Session(pg) as db, db.begin(): watch=watch_game(db,game_id); watch_id=watch.id
    with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second'"))
    job_id,token=claim_next(pg,'mock-worker')
    row['fixture']['date']=(football_game(row).starts_at+timedelta(days=1)).isoformat()
    with Session(pg) as db, db.begin(): upsert_game(db,football_game(row),None)
    with pg.connect() as conn:
        state,due,start=conn.execute(text('SELECT j.status,j.due_at,g.starts_at FROM jobs j JOIN watched_games w ON j.watched_game_id=w.id JOIN games g ON w.game_id=g.id WHERE w.id=:id'),{'id':watch_id}).one()
        assert state=='pending' and due-start==timedelta(minutes=120)
    from backend.worker import apply_work
    assert not apply_work(pg,job_id,token,football_game(row))
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code==200
        csrf=client.get('/api/v1/auth/session').json()['data']['csrf_token']; headers={'origin':ORIGIN,'x-csrf-token':csrf}
        first=client.get('/api/v1/sync-schedules').json()['data']
        assert len(first['items'])==4
        body={'revision':first['revision'],'items':[{'provider':'polymarket','sport':'football','utc_time':'01:00:00','enabled':True},{'provider':'balldontlie','sport':'nba','utc_time':'09:00:00','enabled':True}]}
        assert client.put('/api/v1/sync-schedules',json=body,headers=headers).status_code==200
        assert client.put('/api/v1/sync-schedules',json=body,headers=headers).status_code==409
        assert client.post('/api/v1/games/'+str(game_id)+'/watch',json={},headers=headers).json()['data']['id']==str(watch_id)


def test_worker_processes_due_only_and_preserves_retry_after(pg,monkeypatch):
    from datetime import timedelta
    import httpx
    from sqlalchemy.orm import Session
    from backend.scheduler import watch_game
    from backend.worker import process_one
    from backend.config import get_settings
    game_id,row=setup_scheduler_game(pg,'nba')
    with Session(pg) as db, db.begin(): watch_game(db,game_id)
    monkeypatch.setenv('POLYSPORTS_BALLDONTLIE_KEY','mock-only-key'); get_settings.cache_clear()
    settings=get_settings(); calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(429,headers={'Retry-After':'1200'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not process_one(pg,settings,'mock-worker',client)
        assert not calls
        with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second'"))
        assert process_one(pg,settings,'mock-worker',client)
    with pg.connect() as conn:
        due,now=conn.execute(text('SELECT due_at,clock_timestamp() FROM jobs')).one()
        assert due>now+timedelta(seconds=1195)
        assert conn.execute(text('SELECT state FROM watched_games')).scalar_one()=='active'
    get_settings.cache_clear()


def test_scheduled_jobs_survive_restart_and_future_schedule_changes(pg):
    from datetime import timedelta
    from backend.scheduler import ensure_scheduled, claim_next
    setup_scheduler_game(pg)
    ensure_scheduled(pg)
    with pg.connect() as conn:
        before=conn.execute(text("SELECT count(*) FROM jobs WHERE kind='sync'")).scalar_one()
        assert before>=4
    ensure_scheduled(pg)
    with pg.connect() as conn: assert conn.execute(text("SELECT count(*) FROM jobs WHERE kind='sync'")).scalar_one()==before
    assert claim_next(pg,'mock-restarted-worker') is None
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code==200
        csrf=client.get('/api/v1/auth/session').json()['data']['csrf_token']; headers={'origin':ORIGIN,'x-csrf-token':csrf}
        plan=client.get('/api/v1/sync-schedules').json()['data']
        body={'revision':plan['revision'],'items':[{'provider':'polymarket','sport':'football','utc_time':'03:01:00','enabled':True},{'provider':'balldontlie','sport':'nba','utc_time':'03:02:00','enabled':True}]}
        assert client.put('/api/v1/sync-schedules',json=body,headers=headers).status_code==200
        with pg.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM jobs WHERE kind='sync' AND status='cancelled'")).scalar_one()==before
        first=client.post('/api/v1/sync-runs',json={'sport':'football'},headers=headers).json()['data']
        second=client.post('/api/v1/sync-runs',json={'sport':'football'},headers=headers).json()['data']
        assert first['job_id']==second['job_id']
    ensure_scheduled(pg)
    with pg.connect() as conn:
        due=conn.execute(text("SELECT min(due_at-clock_timestamp()) FROM jobs WHERE kind='sync' AND schedule_id IS NOT NULL AND status='pending'")).scalar_one()
        assert due>timedelta(0)


def test_worker_real_flow_mock_poll_and_scheduled_sync(pg,monkeypatch):
    import httpx
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from backend.scheduler import watch_game
    from backend.worker import process_one
    from backend.config import get_settings
    game_id,row=setup_scheduler_game(pg)
    monkeypatch.setenv('POLYSPORTS_API_FOOTBALL_KEY','mock-only-key'); get_settings.cache_clear(); settings=get_settings()
    with Session(pg) as db, db.begin(): watch_game(db,game_id)
    with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second'"))
    row['fixture']['status']['short']='2H'; row['goals']={'home':1,'away':0}
    def handler(req): return httpx.Response(200,json={'errors':[],'paging':{'current':1,'total':1},'response':[row]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert process_one(pg,settings,'mock-worker',client)
        with pg.connect() as conn:
            due,now,state=conn.execute(text('SELECT j.due_at,clock_timestamp(),w.state FROM jobs j JOIN watched_games w ON j.watched_game_id=w.id')).one()
            assert due>now+timedelta(minutes=9,seconds=55) and state=='active'
            assert conn.execute(text('SELECT status FROM games')).scalar_one()=='live'
        with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second'"))
        row['fixture']['status']['short']='FT'; row['goals']={'home':2,'away':1}
        assert process_one(pg,settings,'mock-worker',client)
    with pg.connect() as conn:
        assert conn.execute(text('SELECT state FROM watched_games')).scalar_one()=='completed'
        assert conn.execute(text('SELECT status FROM jobs')).scalar_one()=='done'
        assert conn.execute(text('SELECT status FROM games')).scalar_one()=='finished'
    get_settings.cache_clear()


def test_worker_sync_job_uses_fenced_run_and_failure_retry(pg,monkeypatch):
    import httpx
    from backend.worker import process_one
    from backend.config import get_settings
    from test_polymarket_football import mock_event
    from datetime import datetime, timedelta, timezone
    setup_scheduler_game(pg)
    monkeypatch.setenv('POLYSPORTS_API_FOOTBALL_KEY','mock-only-key'); get_settings.cache_clear(); settings=get_settings()
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code==200
        csrf=client.get('/api/v1/auth/session').json()['data']['csrf_token']; headers={'origin':ORIGIN,'x-csrf-token':csrf}
        data=client.post('/api/v1/sync-runs',json={'sport':'football'},headers=headers).json()['data']
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429,headers={'Retry-After':'1800'}))) as source:
        assert process_one(pg,settings,'mock-worker',source)
    with pg.connect() as conn:
        status,due,now=conn.execute(text('SELECT status,due_at,clock_timestamp() FROM jobs WHERE id=:id'),{'id':data['job_id']}).one()
        assert status=='pending' and due>now+timedelta(seconds=1795)
        assert conn.execute(text('SELECT status FROM sync_runs WHERE id=:id'),{'id':data['run_id']}).scalar_one()=='failed'
    with pg.begin() as conn: conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE id=:id"),{'id':data['job_id']})
    game=mock_event(when=datetime.now(timezone.utc)+timedelta(hours=2))
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'events':[game]}))) as source:
        assert process_one(pg,settings,'mock-worker',source)
    with pg.connect() as conn:
        assert conn.execute(text('SELECT status FROM jobs WHERE id=:id'),{'id':data['job_id']}).scalar_one()=='done'
        assert conn.execute(text('SELECT status FROM sync_runs WHERE id=:id'),{'id':data['run_id']}).scalar_one()=='succeeded'
        assert conn.execute(text('SELECT array_agg(result ORDER BY attempt_no) FROM job_runs WHERE job_id=:id'),{'id':data['job_id']}).scalar_one()==['failed','succeeded']
    get_settings.cache_clear()
