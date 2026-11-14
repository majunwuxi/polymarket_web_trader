"""均为模拟结算数据，不表示真实市场或真实交易。"""
import httpx
import pytest

from backend.order_resolution import compare_moneyline_score, read_resolution
from backend.sports_sources import SourceError

CONDITION='0x'+'a'*64


def read(body,index=0):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body))) as client:
        return read_resolution(client,CONDITION,index)


def test_resolution_needs_terminal_binary_payout_and_identity():
    row={'condition_id':CONDITION,'status':'resolved','resolved_at':'2026-01-01T00:00:00Z','payouts':[1,0]}
    assert read({'data':[row]})['outcome_won'] is True
    assert read({'data':[row]},1)['outcome_won'] is False
    assert read({'data':[]})['state']=='pending'
    assert read({'data':[{**row,'resolved_at':None}]})['state']=='pending'
    assert read({'data':[{**row,'status':'proposed'}]})['state']=='pending'
    assert read({'data':[{**row,'payouts':[1,1]}]})['state']=='pending'
    with pytest.raises(SourceError,match='resolution_identity_mismatch'):
        read({'data':[{**row,'condition_id':'0x'+'b'*64}]})


def test_score_comparison_refuses_ambiguous_market_and_flags_conflict():
    common={'sport':'nba','question':'Nuggets vs. Pelicans','home':'Pelicans','away':'Nuggets',
            'outcome_label':'Nuggets','home_score':90,'away_score':100,'provider_status':'final'}
    assert compare_moneyline_score(**common,outcome_won=True)=='consistent'
    assert compare_moneyline_score(**common,outcome_won=False)=='conflict'
    assert compare_moneyline_score(**{**common,'question':'Nuggets total points over 99'},outcome_won=True)=='unsupported'
    football={'sport':'football','question':'Will Norway win on 2026-11-14?','home':'Norway','away':'Wales',
              'outcome_label':'Yes','home_score':1,'away_score':0,'provider_status':'FT'}
    assert compare_moneyline_score(**football,outcome_won=True)=='consistent'
    assert compare_moneyline_score(**{**football,'provider_status':'AET'},outcome_won=True)=='unsupported'
