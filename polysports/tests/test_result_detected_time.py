"""赛果首次检测时间的隔离 PostgreSQL 验证。"""
import uuid

from sqlalchemy import text

from test_z_auth_integration import pg, setup_scheduler_game  # noqa: F401
from test_zz_markets_integration import logged_client


def test_game_list_exposes_first_finished_detection_time(pg):
    game_id,_=setup_scheduler_game(pg,'football')
    with pg.begin() as conn:
        conn.execute(text("UPDATE games SET provider='polymarket',external_id='90108495' WHERE id=:id"),{'id':game_id})
        conn.execute(text("""INSERT INTO result_binding_requests (id,game_id,provider,state,candidates,next_attempt_at,updated_at)
            VALUES (:id,:game_id,'api_football','bound','[]'::jsonb,now(),now())"""),{'id':uuid.uuid4(),'game_id':game_id})
        conn.execute(text("""INSERT INTO game_revisions (id,game_id,observed_at,old_status,new_status,home_score,away_score,change_reason)
            VALUES (:a,:game,'2026-11-16T03:00:00Z','live','finished',2,1,'verified_result'),
                   (:b,:game,'2026-11-16T03:05:00Z','finished','finished',2,1,'source_update')"""),
            {'a':uuid.uuid4(),'b':uuid.uuid4(),'game':game_id})
    client,headers=logged_client()
    try:
        items=client.get('/api/v1/games',params={'sport':'football','from':'2026-01-01T00:00:00Z','to':'2027-01-01T00:00:00Z','limit':100}).json()['data']['items']
        row=next(item for item in items if item['id']==str(game_id))
        assert row['result_binding_state']=='bound'
        # 取最早一条带比分的 finished 变更作为首次检测时间，而不是最近一次同步时间。
        assert row['result_detected_at']=='2026-11-16T03:00:00+00:00'
        detail=client.get('/api/v1/games/'+str(game_id)).json()['data']
        assert detail['result_detected_at']=='2026-11-16T03:00:00+00:00'
    finally:
        client.__exit__(None,None,None)
