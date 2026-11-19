"""赛果首次检测时间的隔离 PostgreSQL 验证。"""
import uuid

from sqlalchemy import text

from test_z_auth_integration import pg, setup_scheduler_game  # noqa: F401
from test_zz_markets_integration import logged_client


def test_game_list_exposes_first_finished_detection_time(pg):
    game_id,_=setup_scheduler_game(pg,'football')
    waiting_game_id=uuid.uuid4()
    watch_id=uuid.uuid4()
    due='2026-11-16T05:00:00Z'
    with pg.begin() as conn:
        conn.execute(text("UPDATE games SET provider='polymarket',external_id='90108495' WHERE id=:id"),{'id':game_id})
        conn.execute(text("""INSERT INTO result_binding_requests (id,game_id,provider,state,candidates,next_attempt_at,updated_at)
            VALUES (:id,:game_id,'api_football','bound','[]'::jsonb,now(),now())"""),{'id':uuid.uuid4(),'game_id':game_id})
        conn.execute(text("""INSERT INTO game_revisions (id,game_id,observed_at,old_status,new_status,home_score,away_score,change_reason)
            VALUES (:a,:game,'2026-11-16T03:00:00Z','live','finished',2,1,'verified_result'),
                   (:b,:game,'2026-11-16T03:05:00Z','finished','finished',2,1,'source_update')"""),
            {'a':uuid.uuid4(),'b':uuid.uuid4(),'game':game_id})
        # 已绑定但尚未检测到结果的赛事：暴露下一次排期查询时间。
        conn.execute(text("""INSERT INTO games (id,provider,external_id,sport,home_team_id,away_team_id,starts_at,status,market_available)
            SELECT :new,'polymarket','mock-waiting','football',home_team_id,away_team_id,now()+interval '3 hours','scheduled',true FROM games WHERE id=:id"""),
            {'new':waiting_game_id,'id':game_id})
        conn.execute(text("""INSERT INTO result_binding_requests (id,game_id,provider,state,candidates,next_attempt_at,updated_at)
            VALUES (:id,:game_id,'api_football','bound','[]'::jsonb,now(),now())"""),{'id':uuid.uuid4(),'game_id':waiting_game_id})
        conn.execute(text("INSERT INTO watched_games (id,game_id,state,first_due_at) VALUES (:id,:game,'active',:due)"),
                     {'id':watch_id,'game':waiting_game_id,'due':due})
        conn.execute(text("INSERT INTO jobs (id,kind,dedupe_key,watched_game_id,due_at,status) VALUES (:id,'result_poll',:key,:watch,:due,'pending')"),
                     {'id':uuid.uuid4(),'key':'watch:'+str(watch_id),'watch':watch_id,'due':due})
    client,headers=logged_client()
    try:
        items=client.get('/api/v1/games',params={'sport':'football','from':'2026-01-01T00:00:00Z','to':'2027-01-01T00:00:00Z','limit':100}).json()['data']['items']
        row=next(item for item in items if item['id']==str(game_id))
        assert row['result_binding_state']=='bound'
        # 取最早一条带比分的 finished 变更作为首次检测时间，而不是最近一次同步时间。
        assert row['result_detected_at']=='2026-11-16T03:00:00+00:00'
        waiting=next(item for item in items if item['id']==str(waiting_game_id))
        assert waiting['result_detected_at'] is None
        assert waiting['result_check_at']==due.replace('Z','+00:00')
        detail=client.get('/api/v1/games/'+str(game_id)).json()['data']
        assert detail['result_detected_at']=='2026-11-16T03:00:00+00:00'
    finally:
        client.__exit__(None,None,None)
