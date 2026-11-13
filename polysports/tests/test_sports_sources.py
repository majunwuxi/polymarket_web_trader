"""以下全部是模拟数据；不连接真实体育数据来源。"""
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from backend.sports_sources import SourceError, fetch_schedule

START=datetime(2026,1,1,12,tzinfo=timezone.utc)


def mock_football(ident=1, when='2026-01-01T13:00:00+00:00', status='NS'):
    return {'fixture':{'id':ident,'date':when,'status':{'short':status}},'teams':{'home':{'id':11,'name':'Mock Home','logo':None},'away':{'id':22,'name':'Mock Away','logo':None}},'league':{'id':3,'name':'Mock League','logo':None},'goals':{'home':None,'away':None}}


def mock_nba(ident=1, when='2026-01-02T03:00:00Z'):
    return {'id':ident,'datetime':when,'status_state':'scheduled','home_team':{'id':11,'full_name':'Mock Home'},'visitor_team':{'id':22,'full_name':'Mock Away'},'home_team_score':0,'visitor_team_score':0}


def test_football_pagination_and_utc_window():
    calls=[]
    def handler(request):
        calls.append(request)
        page=int(request.url.params.get('page','1'))
        return httpx.Response(200,json={'errors':[],'paging':{'current':page,'total':2},'response':[mock_football(page, '2026-01-01T12:00:00Z' if page==1 else '2026-01-03T00:00:00Z')]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        games,pages,received=fetch_schedule('api_football','mock-api-key',START,client)
    assert (len(games),pages,received)==(1,2,2)
    assert calls[0].headers['x-apisports-key']=='mock-api-key'
    assert calls[0].url.params['timezone']=='UTC'
    assert calls[1].url.params['page']=='2'


def test_nba_cursor_and_no_fabricated_scores():
    calls=[]
    def handler(request):
        calls.append(request)
        cursor=request.url.params.get('cursor')
        return httpx.Response(200,json={'data':[mock_nba(2 if cursor else 1)],'meta':{} if cursor else {'next_cursor':77}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        games,pages,_=fetch_schedule('balldontlie','mock-api-key',START,client)
    assert pages==2 and len(games)==2
    assert games[0].home_score is None and games[0].away_score is None
    assert calls[1].url.params['cursor']=='77'
    assert calls[0].headers['authorization']=='mock-api-key'


@pytest.mark.parametrize('response,code',[(httpx.Response(401),'http_401'),(httpx.Response(403),'http_403'),(httpx.Response(429),'http_429'),(httpx.Response(200,json={'errors':{'token':'bad'},'paging':{'current':1,'total':1},'response':[]}), 'provider_error'),(httpx.Response(200,json={'errors':[],'response':[]}), 'invalid_pagination')])
def test_source_errors_are_safe(response,code):
    with httpx.Client(transport=httpx.MockTransport(lambda req:response)) as client:
        with pytest.raises(SourceError,match=code): fetch_schedule('api_football','mock-secret-key',START,client)


def test_later_page_failure_and_repeated_cursor_fail():
    count=0
    def handler(req):
        nonlocal count
        count+=1
        return httpx.Response(200,json={'data':[mock_nba(count)],'meta':{'next_cursor':4}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError,match='pagination_loop'): fetch_schedule('balldontlie','mock-key',START,client)
    assert count==2


def test_timeout_empty_and_invalid_fields():
    with httpx.Client(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ReadTimeout('mock')))) as client:
        with pytest.raises(SourceError,match='timeout'): fetch_schedule('balldontlie','mock-key',START,client)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,json={'data':[],'meta':{}}))) as client:
        assert fetch_schedule('balldontlie','mock-key',START,client)==([],1,0)
    row=mock_nba(); row['datetime']='2026-01-01'
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,json={'data':[row],'meta':{}}))) as client:
        with pytest.raises(SourceError,match='invalid_datetime'): fetch_schedule('balldontlie','mock-key',START,client)


def test_inconsistent_football_page_count_is_failure():
    def handler(request):
        page=int(request.url.params.get('page','1'))
        return httpx.Response(200,json={'errors':[],'paging':{'current':page,'total':2 if page==1 else 3},'response':[]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError,match='invalid_pagination'):
            fetch_schedule('api_football','mock-key',START,client)


def test_nba_preseason_filter_is_explicit():
    seen=[]
    def handler(request):
        seen.append(request.url.params.get('season_type'))
        return httpx.Response(200,json={'data':[],'meta':{}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch_schedule('balldontlie','mock-key',START,client,season_type='preseason')
    assert seen==['preseason']


def test_retry_after_is_exposed_without_secrets():
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429,headers={'Retry-After':'3600'}))) as client:
        with pytest.raises(SourceError) as exc:
            fetch_schedule('balldontlie','mock-secret',START,client)
    assert exc.value.code=='http_429' and exc.value.retry_after_seconds==3600
    assert 'mock-secret' not in str(exc.value)
