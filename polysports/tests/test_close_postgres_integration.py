"""隔离 PostgreSQL 和模拟交易所的平仓并发验证，不发送真实订单。"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from sqlalchemy import text
from test_z_auth_integration import pg  # noqa: F401
from test_zzz_trading_integration import env, preview, MockGateway  # noqa: F401


def test_fok_close_is_parent_bound_and_concurrent_requests_do_not_double_send(env,monkeypatch):
    engine,client,headers,body=env
    buy=preview(client,headers,body)
    response=client.post('/api/v1/single-orders',json=buy,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201
    original=response.json()['data']['intent']['id']
    with engine.begin() as conn:
        conn.execute(text("UPDATE trade_intents SET state='filled',matched_size=5 WHERE id=:id"),{'id':uuid.UUID(original)})
    MockGateway.posts=0
    monkeypatch.setattr(MockGateway,'sign_fok',lambda self,*args:object(),raising=False)
    def post(self,signed):
        with self.mutex:type(self).posts+=1
        return SimpleNamespace(ok=True,order_id='0x'+'c'*64)
    monkeypatch.setattr(MockGateway,'post',post)
    close={**body,'side':'SELL','order_type':'FOK','parent_trade_id':original}
    close=preview(client,headers,close)
    def submit(_):return client.post('/api/v1/single-orders',json=close,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert sorted(result.status_code for result in results)==[201,409]
    assert MockGateway.posts==1
    with engine.connect() as conn:
        row=conn.execute(text('SELECT parent_trade_id,order_type,side FROM trade_intents WHERE parent_trade_id=:id'),{'id':uuid.UUID(original)}).one()
        assert row.parent_trade_id==uuid.UUID(original) and row.order_type=='FOK' and row.side=='SELL'
