"""6H：仅本机隔离 PostgreSQL、模拟赛事和模拟成交证据。"""
import csv
import io
import uuid
from datetime import datetime, timezone
from openpyxl import load_workbook
from sqlalchemy import text
from test_z_auth_integration import pg  # noqa: F401
from test_zzz_trading_integration import env, preview  # noqa: F401
from backend.history_api import spreadsheet_text


def test_spreadsheet_formula_defense():
    for value in ('=1+1','  +1','\t@cmd','\r-1'):
        assert spreadsheet_text(value).startswith("'")
    assert spreadsheet_text('012345678901234567890')=='012345678901234567890'


def test_history_statistics_and_exports_cover_all_filtered_rows(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body)
    response=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201
    intent=uuid.UUID(response.json()['data']['intent']['id'])
    extras=[uuid.uuid4() for _ in range(105)]
    with pg.begin() as c:
        game=c.execute(text('SELECT game_id FROM trade_intents WHERE id=:id'),{'id':intent}).scalar_one()
        team=c.execute(text('SELECT home_team_id FROM games WHERE id=:id'),{'id':game}).scalar_one()
        c.execute(text("UPDATE teams SET name_zh='=MOCK_FORMULA',translation_status='verified' WHERE id=:id"),{'id':team})
        c.execute(text("INSERT INTO trade_fills (id,intent_id,external_trade_id,size,price,status,matched_at,observed_at) VALUES (:id,:intent,'mock-confirmed',1,0.5,'CONFIRMED',now(),now())"),{'id':uuid.uuid4(),'intent':intent})
        c.execute(text("INSERT INTO trade_fills (id,intent_id,external_trade_id,size,price,status,matched_at,observed_at) VALUES (:id,:intent,'mock-pending',1,0.5,'MATCHED',now(),now())"),{'id':uuid.uuid4(),'intent':intent})
        for ident in extras:
            c.execute(text("INSERT INTO games (id,provider,external_id,sport,home_team_id,away_team_id,starts_at,status) SELECT :id,provider,:external_id,sport,home_team_id,away_team_id,starts_at,status FROM games WHERE id=:base"),{'id':ident,'external_id':'mock-extra-'+str(ident),'base':game})
    try:
        page=client.get('/api/v1/history/games?limit=10')
        assert page.status_code==200 and len(page.json()['data']['items'])==10 and page.json()['data']['next_cursor']
        stats=client.get('/api/v1/statistics').json()['data']
        assert stats['games_total']>=106 and stats['confirmed_fill_count']==1 and stats['realized_pnl'] is None
        history=client.get('/api/v1/history/trades?kind=single').json()['data']['items']
        assert any(x['id']==str(intent) and x['confirmed_fills']==1 for x in history)
        result=client.get('/api/v1/exports/games.csv')
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
        lines=list(csv.reader(io.StringIO(result.content.decode('utf-8-sig'))))
        assert len(lines)>=107 and any("'=MOCK_FORMULA" in row for row in lines[1:])
        xlsx=client.get('/api/v1/exports/games.xlsx')
        assert xlsx.status_code==200
        workbook=load_workbook(io.BytesIO(xlsx.content),read_only=True,data_only=False)
        cells=list(workbook.active.values)
        assert len(cells)==len(lines) and any("'=MOCK_FORMULA" in row for row in cells[1:])
        trade=client.get('/api/v1/exports/trades.csv')
        assert trade.status_code==200 and str(intent) in trade.text
        invalid=client.get('/api/v1/history/games?from=2026-01-01T00:00:00')
        assert invalid.status_code==422
    finally:
        with pg.begin() as c:
            c.execute(text('DELETE FROM games WHERE id = ANY(:ids)'),{'ids':extras})


def test_system_status_and_audit_do_not_expose_secrets(env):
    pg,client,headers,body=env
    status=client.get('/api/v1/system/status')
    assert status.status_code==200 and status.json()['data']['database']['status']=='ok'
    assert 'private_key' not in status.text and 'mock-password' not in status.text
    audit=client.get('/api/v1/audit-events?limit=1')
    assert audit.status_code==200 and 'details' not in audit.text


def test_all_documented_routes_exist_in_openapi():
    import re
    from pathlib import Path
    from backend.main import create_app
    paths=create_app().openapi()['paths']
    actual={(method.upper(),re.sub(r'\{[^}]+\}','{}',path)) for path,methods in paths.items() for method in methods if method in ('get','post','put','patch','delete')}
    doc=Path('../API.md').read_text(encoding='utf-8')
    documented={(method,re.sub(r'\{[^}]+\}','{}',path if path.startswith('/api/v1') else '/api/v1'+path)) for method,path in re.findall(r'`(GET|POST|PUT|PATCH|DELETE) (/[^` ]+)`',doc)}
    assert documented-actual==set()


def test_unauthenticated_history_and_exports_are_rejected(pg):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    # 隔离数据库；拒绝认证发生在导出查询之前。
    with TestClient(create_app(),base_url='https://example.test',raise_server_exceptions=False) as client:
        for path in ('/api/v1/history/games','/api/v1/statistics','/api/v1/exports/trades.xlsx'):
            assert client.get(path).status_code==401


def test_combo_statistics_and_export_use_confirmed_state(sample,monkeypatch):
    from backend import combo_service
    from test_zzzzz_combo_state import FakeGateway,catalog
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-only')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    before=client.get('/api/v1/statistics?sport=nba').json()['data']
    assert before['confirmed_combo_count']==0
    # 模拟上游确认状态；不调用真实服务。
    with engine.begin() as db:db.execute(text("UPDATE combo_rfqs SET state='confirmed' WHERE id=:id"),{'id':ident})
    after=client.get('/api/v1/statistics?sport=nba').json()['data']
    assert after['confirmed_combo_count']==1 and after['realized_pnl'] is None
    history=client.get('/api/v1/history/trades?kind=combo&sport=nba').json()['data']['items']
    assert any(row['id']==str(ident) and row['kind']=='combo' for row in history)
    output=client.get('/api/v1/exports/trades.xlsx?kind=combo&sport=nba')
    assert output.status_code==200
    workbook=load_workbook(io.BytesIO(output.content),read_only=True)
    assert any(str(ident) in row for row in workbook.active.values)


from test_zzzzz_combo_state import sample  # noqa: E402,F401


def test_trade_cursor_handles_equal_timestamp_and_uuid_across_kinds(env):
    pg,client,headers,body=env
    order=client.post('/api/v1/single-orders',json=preview(client,headers,body),headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    ident=uuid.UUID(order.json()['data']['intent']['id'])
    with pg.begin() as db:
        db.execute(text("INSERT INTO combo_rfqs (id,wallet_id,wallet_address,client_request_id,fingerprint,direction,quantity,state,created_at) SELECT id,wallet_id,wallet_address,:key,:fp,'BUY',10,'failed',created_at FROM trade_intents WHERE id=:id"),{'id':ident,'key':uuid.uuid4(),'fp':'e'*64})
    try:
        first=client.get('/api/v1/history/trades?limit=1').json()['data']
        second=client.get('/api/v1/history/trades',params={'limit':1,'cursor':first['next_cursor']}).json()['data']
        assert {first['items'][0]['kind'],second['items'][0]['kind']}=={'single','combo'}
        assert first['items'][0]['id']==second['items'][0]['id']==str(ident)
    finally:
        with pg.begin() as db:db.execute(text('DELETE FROM combo_rfqs WHERE id=:id'),{'id':ident})


def test_excel_splits_sheets_without_dropping_rows(monkeypatch):
    from backend import history_api
    monkeypatch.setattr(history_api,'EXCEL_ROWS',3)
    data=b''.join(history_api.excel_bytes(['header'],iter([[str(i)] for i in range(5)])))
    book=load_workbook(io.BytesIO(data),read_only=True)
    rows=[row[0] for sheet in book for row in list(sheet.values)[1:]]
    assert rows==['0','1','2','3','4'] and len(book.sheetnames)==3


def test_existing_filters_match_documented_contract(env):
    from datetime import timedelta
    pg,client,headers,body=env
    submitted=client.post('/api/v1/single-orders',json=preview(client,headers,body),headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert submitted.status_code==201
    intent=submitted.json()['data']['intent'];now=datetime.now(timezone.utc)
    watched=client.get('/api/v1/watches?sport=nba&state=active&limit=1')
    assert watched.status_code==200 and len(watched.json()['data']['items'])==1
    jobs=client.get('/api/v1/jobs',params={'kind':'result_poll','game_id':intent['game_id']})
    assert jobs.status_code==200 and jobs.json()['data']['items']
    games=client.get('/api/v1/games',params={'sport':'nba','watched':'true','from':'2020-01-01T00:00:00Z','to':'2100-01-01T00:00:00Z'})
    assert games.status_code==200 and any(x['id']==intent['game_id'] for x in games.json()['data']['items'])
    trades=client.get('/api/v1/trade-intents',params={'from':(now-timedelta(days=1)).isoformat(),'to':(now+timedelta(days=1)).isoformat()})
    assert trades.status_code==200 and any(x['id']==intent['id'] for x in trades.json()['data']['items'])
    assert client.get('/api/v1/trade-intents?from=2026-01-01T00:00:00').status_code==422


def test_sync_log_filters_and_pagination(env):
    pg,client,headers,body=env
    ids=[uuid.uuid4(),uuid.uuid4()]
    with pg.begin() as db:
        for ident in ids:
            db.execute(text("INSERT INTO sync_runs (id,provider,sport,trigger_type,scheduled_for,status) VALUES (:id,'balldontlie','nba','manual',now(),'queued')"),{'id':ident})
    try:
        first=client.get('/api/v1/sync-runs',params={'sport':'nba','status':'queued','limit':1}).json()['data']
        assert first['items'][0]['started_at'] is None and first['next_cursor']
        second=client.get('/api/v1/sync-runs',params={'sport':'nba','status':'queued','limit':1,'cursor':first['next_cursor']}).json()['data']
        assert second['items'][0]['id']!=first['items'][0]['id']
        detail=client.get('/api/v1/sync-runs/'+first['items'][0]['id'])
        assert detail.status_code==200 and detail.json()['data']['status']=='queued'
    finally:
        with pg.begin() as db:db.execute(text('DELETE FROM sync_runs WHERE id = ANY(:ids)'),{'ids':ids})


def test_openapi_documents_auth_csrf_and_export_types():
    from backend.main import create_app
    schema=create_app().openapi();paths=schema['paths']
    assert paths['/api/v1/history/games']['get']['security']==[{'AdminSession':[]}]
    assert 'security' not in paths['/api/v1/health/live']['get']
    operation=paths['/api/v1/single-orders']['post']
    assert {'Origin','X-CSRF-Token','Idempotency-Key'}.issubset({item['name'] for item in operation['parameters']})
    assert {'200','201','202','401','403'}.issubset(operation['responses'])
    assert 'text/csv' in paths['/api/v1/exports/games.csv']['get']['responses']['200']['content']
