"""协议样本均为模拟数据，不代表真实市场、比赛或价格。"""
import json
from datetime import datetime, timezone
import httpx
import pytest
from backend.gamma import discover, parse_event, parse_market
from backend.sports_sources import SourceError

START=datetime(2026,1,1,12,tzinfo=timezone.utc)
MOCK_BIG='1'+'2'*76


def mock_market(ident='100',version='v1'):
    return {'id':ident,'conditionId':'0x'+'a'*64,'question':'Mock match market','version':version,'gameStartTime':START.isoformat(),'outcomes':json.dumps(['Yes','No']),'clobTokenIds':json.dumps([MOCK_BIG,'123']),'positionIds':['456','789'],'acceptingOrders':True}


def mock_event(ident='10',market=None,reverse=False):
    return {'id':ident,'tags':[{'slug':'nba'}],'title':'Mock Home vs Mock Away','slug':'mock-match','startTime':START.isoformat(),'teams':[{'name':'Mock Home','ordering':'away' if reverse else 'home'},{'name':'Mock Away','ordering':'home' if reverse else 'away'}],'markets':[market or mock_market()]}


def test_protocol_identifiers_and_no_precision_loss():
    v1=parse_market(mock_market())
    assert v1.outcomes[0].asset_id==MOCK_BIG and v1.outcomes[0].kind=='ctf_token'
    v2=parse_market(mock_market(version='v2'))
    assert [x.asset_id for x in v2.outcomes]==['456','789'] and v2.outcomes[0].kind=='position'
    unknown=parse_market(mock_market(version='v3'))
    assert unknown.version is None and unknown.outcomes==()
    raw=mock_market(version='v2');raw.pop('positionIds')
    with pytest.raises(SourceError):parse_market(raw)


def test_invalid_and_floating_asset_never_accepted():
    raw=mock_market();raw['clobTokenIds']=json.dumps([1.5,'2'])
    with pytest.raises(SourceError,match='invalid_asset_id'):parse_market(raw)
    raw=mock_market();raw['conditionId']='0xabc'
    with pytest.raises(SourceError,match='invalid_condition_id'):parse_market(raw)


def test_teams_sport_time_and_reversal_are_not_auto_confirmed():
    event,ordered=parse_event(mock_event(),'nba',START,['Mock Home'],['Mock Away'])
    assert ordered and event.home=='Mock Home'
    _,ordered=parse_event(mock_event(reverse=True),'nba',START,['Mock Home'],['Mock Away'])
    assert not ordered
    assert parse_event(mock_event(),'football',START,['Mock Home'],['Mock Away']) is None
    assert parse_event(mock_event(),'nba',START,['Other Home'],['Mock Away']) is None
    raw=mock_event();raw['startTime']='2026-01-02T12:00:00Z'
    assert parse_event(raw,'nba',START,['Mock Home'],['Mock Away']) is None


@pytest.mark.parametrize(('value','expected'), [('123','123'),(123,'123'),(True,None),('invalid',None),(None,None)])
def test_game_id_is_preserved_only_when_valid(value,expected):
    raw=mock_event(); raw['gameId']=value
    event,_=parse_event(raw,'nba',START,['Mock Home'],['Mock Away'])
    assert event.game_id==expected


def test_polymarket_candidates_require_same_game_id():
    from backend.markets import same_source_game
    raw=mock_event(); raw['gameId']='123'
    event,_=parse_event(raw,'nba',START,['Mock Home'],['Mock Away'])
    assert same_source_game('polymarket','123',event)
    assert not same_source_game('polymarket','124',event)
    raw.pop('gameId')
    missing,_=parse_event(raw,'nba',START,['Mock Home'],['Mock Away'])
    assert not same_source_game('polymarket','123',missing)
    assert same_source_game('balldontlie','123',missing)


def test_keyset_multiple_pages_and_loop_safeguards():
    calls=[]
    def handler(req):
        calls.append(req)
        if len(calls)==1: return httpx.Response(200,json={'events':[mock_event('10')],'next_cursor':'mock-next'})
        if len(calls)==2:
            event=mock_event('11',mock_market('101'));return httpx.Response(200,json={'events':[event],'next_cursor':'mock-last'})
        return httpx.Response(200,json={'events':[]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        events=discover(client,'nba',START,['Mock Home'],['Mock Away'],page_size=1)
    assert len(events)==2 and calls[1].url.params['after_cursor']=='mock-next'
    assert calls[0].url.path=='/events/keyset' and 'start_time_min' in calls[0].url.params
    def repeated(req):return httpx.Response(200,json={'events':[mock_event()],'next_cursor':'same'})
    with httpx.Client(transport=httpx.MockTransport(repeated)) as client:
        with pytest.raises(SourceError,match='pagination_duplicate'):discover(client,'nba',START,['Mock Home'],['Mock Away'])


def test_unexpected_or_failed_page_does_not_return_partial():
    calls=0
    def handler(req):
        nonlocal calls; calls+=1
        return httpx.Response(200,json={'events':[mock_event()],'next_cursor':'mock-cursor'}) if calls==1 else httpx.Response(429)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError,match='http_429'):discover(client,'nba',START,['Mock Home'],['Mock Away'])


def test_wallet_config_rejects_private_keys():
    from pydantic import ValidationError
    from backend.config import WalletConfig
    with pytest.raises(ValidationError):
        WalletConfig(label='mock',wallet_address='0x'+'1'*40,signer_address='0x'+'2'*40,wallet_type='EOA',private_key='mock-never-real')


def test_short_final_page_without_cursor_is_complete():
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'events':[]}))) as client:
        assert discover(client,'nba',START,['Mock Home'],['Mock Away']) == []


def test_full_page_without_cursor_is_not_complete_success():
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'events':[mock_event()]}))) as client:
        with pytest.raises(SourceError,match='invalid_pagination'):
            discover(client,'nba',START,['Mock Home'],['Mock Away'],page_size=1)
