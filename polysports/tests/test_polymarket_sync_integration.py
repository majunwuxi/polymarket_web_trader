"""模拟 Polymarket，同步仅写入隔离 PostgreSQL。"""
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Game, SyncRun
from backend.sports_sync import run_sync
from backend.main import create_app
from test_z_auth_integration import pg, log_in, ORIGIN  # noqa: F401


def test_mock_repeated_sync_and_failed_sync_preserve_history(pg):
    start = datetime.now(timezone.utc)
    kickoff = (start + timedelta(hours=1)).isoformat()
    event = {'id':'98765001', 'gameId':98765002, 'active':True, 'closed':False,
             'startTime':kickoff, 'tags':[{'slug':'soccer','id':'100350'},{'slug':'EPL','id':'306'}], 'series':[{'id':'10188'}],
             'teams':[{'id':98765003,'name':'Mock Home','ordering':'home'},
                      {'id':98765004,'name':'Mock Away','ordering':'away'}],
             'markets':[{'id':'98765005','sportsMarketType':'moneyline',
                         'active':True,'closed':False,'gameStartTime':kickoff}]}
    def sync(body, when):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,json=body))) as client:
            return run_sync(pg,'polymarket','',client=client,now=when)
    first=sync({'events':[event]},start)
    second=sync({'events':[event]},start+timedelta(seconds=1))
    with Session(pg) as db:
        games=list(db.scalars(select(Game).where(Game.provider=='polymarket',Game.external_id=='98765002')))
        assert len(games)==1 and games[0].market_available is True
        assert games[0].home_score is None and games[0].status=='scheduled'
        assert games[0].league_id is not None
        assert db.get(SyncRun,first).status==db.get(SyncRun,second).status=='succeeded'
        game_id=str(games[0].id)
    with TestClient(create_app(),base_url=ORIGIN) as client:
        assert log_in(client).status_code==200
        items=client.get('/api/v1/games',params={'sport':'football'}).json()['data']['items']
        assert any(x['id']==game_id and x['provider']=='polymarket' for x in items)
        csrf=client.get('/api/v1/auth/session').json()['data']['csrf_token']
        response=client.post('/api/v1/games/'+game_id+'/watch',json={},headers={'origin':ORIGIN,'x-csrf-token':csrf})
        assert response.status_code==409 and response.json()['error']['code']=='BINDING_REQUIRED'
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        failed=run_sync(pg,'polymarket','',client=client,now=start+timedelta(seconds=2))
    with Session(pg) as db:
        assert db.get(SyncRun,failed).status=='failed'
        assert db.scalar(select(Game).where(Game.provider=='polymarket',Game.external_id=='98765002')).market_available is True
    sync({'events':[]},start+timedelta(seconds=3))
    with Session(pg) as db:
        game=db.scalar(select(Game).where(Game.provider=='polymarket',Game.external_id=='98765002'))
        assert game is not None and game.market_available is False


def test_mock_nba_sync_does_not_override_football_or_duplicate(pg):
    start=datetime.now(timezone.utc)
    raw={'id':'88765001','gameId':88765002,'active':True,'closed':False,
         'startTime':(start+timedelta(hours=1)).isoformat(),'tags':[{'slug':'nba'}],
         'teams':[{'id':88765003,'name':'Mock Home','ordering':'home','league':'nba'},
                  {'id':88765004,'name':'Mock Away','ordering':'away','league':'nba'}]}
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'events':[raw]}))) as client:
        first=run_sync(pg,'polymarket','',client=client,now=start,sport='nba')
        second=run_sync(pg,'polymarket','',client=client,now=start+timedelta(seconds=1),sport='nba')
    with Session(pg) as db:
        games=list(db.scalars(select(Game).where(Game.provider=='polymarket',Game.external_id=='88765002')))
        assert len(games)==1 and games[0].sport=='nba' and games[0].market_available
        assert games[0].home_score is None and games[0].away_score is None
        assert db.get(SyncRun,first).status==db.get(SyncRun,second).status=='succeeded'
