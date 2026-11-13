"""隔离 PostgreSQL 和明确标识的模拟 Gamma 数据；不访问真实交易接口。"""
import json
from datetime import timedelta
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend.config import get_settings
from backend.main import create_app
from backend.markets import begin_refresh, refresh_game
from test_gamma import mock_event, mock_market
from test_z_auth_integration import pg, setup_scheduler_game, log_in, ORIGIN


def prepare(pg):
    with pg.begin() as conn:
        for table in ('market_outcomes','market_links','market_refreshes','poly_markets','poly_events'):
            conn.execute(text(f'DELETE FROM {table}'))
    game_id,_=setup_scheduler_game(pg,'nba')
    with pg.connect() as conn: start=conn.execute(text('SELECT starts_at FROM games WHERE id=:id'),{'id':game_id}).scalar_one()
    market=mock_market();market['gameStartTime']=start.isoformat()
    event=mock_event(market=market);event['startTime']=start.isoformat()
    return game_id,event


def refresh_mock(pg,game_id,event):
    assert begin_refresh(pg,game_id)=='started'
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'events':[event],'next_cursor':None}))) as source:
        refresh_game(pg,game_id,source)


def logged_client():
    client=TestClient(create_app(),base_url=ORIGIN);client.__enter__()
    assert log_in(client).status_code==200
    token=client.get('/api/v1/auth/session').json()['data']['csrf_token']
    return client,{'origin':ORIGIN,'x-csrf-token':token}


def test_mapping_confirmation_expiry_failed_refresh_and_audit(pg):
    game_id,event=prepare(pg);refresh_mock(pg,game_id,event)
    client,headers=logged_client()
    try:
        body=client.get(f'/api/v1/games/{game_id}/markets').json()['data']
        assert body['refresh']['status']=='succeeded' and len(body['items'])==1
        item=body['items'][0];link=item['link']['id']
        assert item['link']['state']=='candidate' and item['link']['can_confirm']
        assert item['market']['protocol_version']=='v1' and item['market']['outcomes'][0]['asset_id']==('1'+'2'*76)
        assert not item['link']['trade_ready'] and item['market']['combo_enabled'] is None
        response=client.post(f'/api/v1/market-links/{link}/confirm',json={},headers=headers)
        assert response.status_code==200 and response.json()['data']['link']['state']=='confirmed'
        assert client.post(f'/api/v1/market-links/{link}/confirm',json={},headers=headers).status_code==200
        with pg.connect() as conn: assert conn.execute(text("SELECT count(*) FROM audit_events WHERE action='market_link_confirmed'")).scalar_one()==1
        with pg.begin() as conn: conn.execute(text("UPDATE games SET starts_at=starts_at+interval '1 day' WHERE id=:id"),{'id':game_id})
        assert client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]['link']['state']=='stale'
        assert client.post(f'/api/v1/market-links/{link}/confirm',json={},headers=headers).status_code==409
        assert begin_refresh(pg,game_id)=='started'
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429))) as source: refresh_game(pg,game_id,source)
        result=client.get(f'/api/v1/games/{game_id}/markets').json()['data']
        assert result['refresh']['status']=='failed' and result['refresh']['error_code']=='http_429'
        assert result['items'][0]['link']['state']=='stale'
    finally: client.__exit__(None,None,None)


def test_reversed_team_order_cannot_confirm_and_rejection_is_audited(pg):
    game_id,event=prepare(pg)
    event['teams'][0]['ordering']='away';event['teams'][1]['ordering']='home'
    refresh_mock(pg,game_id,event)
    client,headers=logged_client()
    try:
        item=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]
        assert item['link']['evidence']['ordered'] is False and not item['link']['can_confirm']
        link=item['link']['id']
        assert client.post(f'/api/v1/market-links/{link}/confirm',json={},headers=headers).status_code==409
        assert client.post(f'/api/v1/market-links/{link}/reject',json={},headers=headers).status_code==200
        assert client.post(f'/api/v1/market-links/{link}/confirm',json={},headers=headers).status_code==409
    finally: client.__exit__(None,None,None)


def test_multiple_candidates_and_conflict_require_manual_selection(pg):
    game_id,event=prepare(pg)
    with pg.begin() as conn:
        other=conn.execute(text("""INSERT INTO games(id,provider,external_id,sport,home_team_id,away_team_id,starts_at,status)
        SELECT gen_random_uuid(),provider,'mock-other',sport,home_team_id,away_team_id,starts_at,status FROM games WHERE id=:id RETURNING id"""),{'id':game_id}).scalar_one()
    refresh_mock(pg,game_id,event);refresh_mock(pg,other,event)
    client,headers=logged_client()
    try:
        first=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]['link']['id']
        second=client.get(f'/api/v1/games/{other}/markets').json()['data']['items'][0]['link']['id']
        assert client.post(f'/api/v1/market-links/{first}/confirm',json={},headers=headers).status_code==200
        assert client.post(f'/api/v1/market-links/{second}/confirm',json={},headers=headers).status_code==409
        assert client.get(f'/api/v1/games/{other}/markets').json()['data']['items'][0]['link']['state']=='candidate'
    finally: client.__exit__(None,None,None)


def test_wallet_configuration_returns_unknown_without_actions(pg,monkeypatch):
    wallets=[{'label':'模拟钱包','wallet_address':'0x'+'1'*40,'signer_address':'0x'+'2'*40,'wallet_type':'POLY_PROXY'}]
    monkeypatch.setenv('POLYSPORTS_WALLETS',json.dumps(wallets,ensure_ascii=False));get_settings.cache_clear()
    client,headers=logged_client()
    try:
        result=client.get('/api/v1/wallets').json()['data']['items']
        assert len(result)==1 and result[0]['label']=='模拟钱包'
        state=client.get('/api/v1/wallets/'+result[0]['id']+'/status').json()['data']
        assert state['collateral_balance'] is None and state['allowance_status']=='unknown' and state['builder_access']=='unknown'
    finally:
        client.__exit__(None,None,None);get_settings.cache_clear()


def test_v2_assets_and_expired_snapshot_cannot_confirm(pg):
    game_id,event=prepare(pg)
    market=mock_market(version='v2');market['gameStartTime']=event['startTime'];event['markets']=[market]
    refresh_mock(pg,game_id,event)
    client,headers=logged_client()
    try:
        item=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]
        assert item['market']['protocol_version']=='v2'
        assert [x['asset_id'] for x in item['market']['outcomes']]==['456','789']
        assert all(x['asset_kind']=='position' for x in item['market']['outcomes'])
        with pg.begin() as conn: conn.execute(text("UPDATE market_refreshes SET finished_at=clock_timestamp()-interval '16 minutes' WHERE game_id=:id"),{'id':game_id})
        assert client.post('/api/v1/market-links/'+item['link']['id']+'/confirm',json={},headers=headers).status_code==409
    finally: client.__exit__(None,None,None)


def test_changed_asset_fails_refresh_and_preserves_previous_records(pg):
    game_id,event=prepare(pg);refresh_mock(pg,game_id,event)
    client,headers=logged_client()
    try:
        link=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]['link']['id']
        assert client.post('/api/v1/market-links/'+link+'/confirm',json={},headers=headers).status_code==200
        event['markets'][0]['clobTokenIds']=json.dumps(['10','20'])
        refresh_mock(pg,game_id,event)
        data=client.get(f'/api/v1/games/{game_id}/markets').json()['data']
        assert data['refresh']['status']=='failed' and data['refresh']['error_code']=='market_outcomes_changed'
        assert data['items'][0]['link']['state']=='stale' and not data['items'][0]['link']['trade_ready']
    finally: client.__exit__(None,None,None)


def test_ambiguous_team_alias_is_visible_and_requires_manual_confirmation(pg):
    import uuid
    game_id,event=prepare(pg)
    with pg.begin() as conn:
        conn.execute(text("INSERT INTO teams(id,sport,source_name,translation_status) VALUES (:id,'nba','Mock Home','missing')"),{'id':uuid.uuid4()})
    refresh_mock(pg,game_id,event)
    client,headers=logged_client()
    try:
        item=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]
        assert item['link']['evidence']['home_name_ambiguous'] is True
        assert item['link']['state']=='candidate'
        assert client.post('/api/v1/market-links/'+item['link']['id']+'/confirm',json={},headers=headers).status_code==200
    finally:client.__exit__(None,None,None)


def test_database_rejects_invalid_market_identifiers_and_sport(pg):
    import pytest
    from sqlalchemy.exc import IntegrityError
    game_id,event=prepare(pg);refresh_mock(pg,game_id,event)
    with pytest.raises(IntegrityError):
        with pg.begin() as conn: conn.execute(text("UPDATE poly_markets SET condition_id='0xabc'"))
    with pytest.raises(IntegrityError):
        with pg.begin() as conn: conn.execute(text("UPDATE poly_markets SET protocol_version='v3'"))
    with pytest.raises(IntegrityError):
        with pg.begin() as conn: conn.execute(text("UPDATE market_outcomes SET asset_id='1.2'"))
    with pytest.raises(IntegrityError):
        with pg.begin() as conn: conn.execute(text("UPDATE poly_events SET sport='nfl'"))


def test_new_alias_ambiguity_invalidates_prior_confirmation(pg):
    import uuid
    game_id,event=prepare(pg);refresh_mock(pg,game_id,event)
    client,headers=logged_client()
    try:
        item=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]
        assert client.post('/api/v1/market-links/'+item['link']['id']+'/confirm',json={},headers=headers).status_code==200
        with pg.begin() as conn:
            conn.execute(text("INSERT INTO teams(id,sport,source_name,translation_status) VALUES (:id,'nba','Mock Home','missing')"),{'id':uuid.uuid4()})
        refreshed=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]
        assert refreshed['link']['state']=='stale' and not refreshed['link']['trade_ready']
    finally:client.__exit__(None,None,None)


def test_authenticated_refresh_endpoint_runs_background_discovery(pg,monkeypatch):
    from backend import market_api
    game_id,event=prepare(pg)
    def mock_refresh(engine, selected):
        assert selected==game_id
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'events':[event],'next_cursor':None}))) as source:
            refresh_game(engine,selected,source)
    monkeypatch.setattr(market_api,'refresh_game',mock_refresh)
    client,headers=logged_client()
    try:
        assert client.post(f'/api/v1/games/{game_id}/markets/refresh',json={}).status_code==403
        result=client.post(f'/api/v1/games/{game_id}/markets/refresh',json={},headers=headers)
        assert result.status_code==202 and result.json()['data']['status']=='refreshing'
        assert client.get(f'/api/v1/games/{game_id}/markets').json()['data']['refresh']['status']=='succeeded'
    finally:client.__exit__(None,None,None)
