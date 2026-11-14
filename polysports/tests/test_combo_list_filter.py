"""筛选查询的模拟测试，不连接数据库或交易接口。"""
from types import SimpleNamespace
from sqlalchemy.dialects import postgresql
from backend import combo_api


def test_confirmed_filter_applied_in_database_before_limit(monkeypatch):
    seen=[]
    class SessionMock:
        def __init__(self,engine): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def scalars(self,query):
            seen.append(query)
            return []
    monkeypatch.setattr(combo_api,'Session',SessionMock)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=object())),state=SimpleNamespace(request_id='mock'))
    result=combo_api.list_combos(request,limit=1,cursor=None,state='confirmed')
    assert result['data']['items']==[]
    compiled=seen[0].compile(dialect=postgresql.dialect())
    assert 'combo_rfqs.state =' in str(compiled)
    assert 'confirmed' in compiled.params.values()
    assert 'LIMIT' in str(compiled)


def test_unfiltered_combo_inquiry_list_remains_available(monkeypatch):
    seen=[]
    class SessionMock:
        def __init__(self,engine): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def scalars(self,query): seen.append(query); return []
    monkeypatch.setattr(combo_api,'Session',SessionMock)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=object())),state=SimpleNamespace(request_id='mock'))
    combo_api.list_combos(request,limit=1,cursor=None,state=None)
    assert 'WHERE' not in str(seen[0])
