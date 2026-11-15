"""均为模拟结算数据，不表示真实市场或真实交易。"""
from decimal import Decimal
import httpx
import pytest

from backend.order_resolution import compare_moneyline_score, judge_moneyline_score, read_resolution, settlement_amounts
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


def test_score_judgement_is_independent_of_market_result():
    nba={'sport':'nba','question':'Nuggets vs. Pelicans','home':'Pelicans','away':'Nuggets',
         'outcome_label':'Nuggets','home_score':90,'away_score':100,'provider_status':'final'}
    assert judge_moneyline_score(**nba) is True
    assert judge_moneyline_score(**{**nba,'outcome_label':'Pelicans'}) is False
    assert judge_moneyline_score(**{**nba,'question':'Nuggets total points over 99'}) is None
    assert judge_moneyline_score(**{**nba,'home_score':None}) is None
    football={'sport':'football','question':'Will Norway win on 2026-11-14?','home':'Norway','away':'Wales',
              'outcome_label':'Yes','home_score':1,'away_score':0,'provider_status':'FT'}
    assert judge_moneyline_score(**football) is True
    assert judge_moneyline_score(**{**football,'outcome_label':'No'}) is False
    assert judge_moneyline_score(**{**football,'provider_status':'AET'}) is None


def settle(**kwargs):
    values=dict(side='BUY',matched_size=Decimal('3'),fills=[],market_outcome_won=True,result_outcome_won=True)
    return settlement_amounts(**(values|kwargs))


def test_two_results_agree_and_confirm_settlement_amount():
    win=settle()
    assert win['agreement']=='consistent' and win['settlement_amount']=='3' and win['currency']=='pUSD'
    assert win['market_settlement_amount']=='3' and win['result_settlement_amount']=='3'
    assert '一致' in win['notice']
    assert win['cost_basis'] is None and win['realized_pnl'] is None and win['cost_verified'] is False
    assert '成交明细' in win['reason']
    assert settle(market_outcome_won=False,result_outcome_won=False)['settlement_amount']=='0'


def test_conflicting_results_keep_both_amounts_without_confirming():
    clash=settle(result_outcome_won=False)
    assert clash['agreement']=='conflict' and clash['settlement_amount'] is None
    assert clash['market_settlement_amount']=='3' and clash['result_settlement_amount']=='0'
    assert clash['cost_basis'] is None and '人工核实' in clash['reason']


def test_single_result_is_labelled_and_never_assumed_to_agree():
    market_only=settle(result_outcome_won=None)
    assert market_only['agreement']=='market_only' and market_only['settlement_amount']=='3'
    assert '人工核实' in market_only['notice']
    result_only=settle(market_outcome_won=None)
    assert result_only['agreement']=='result_only' and result_only['result_settlement_amount']=='3'
    assert result_only['settlement_amount'] is None and '预计' in result_only['reason']
    assert settle(market_outcome_won=None,result_outcome_won=None)['agreement']=='unavailable'


def test_settlement_cost_and_pnl_require_complete_confirmed_fills():
    fills=[{'size':Decimal('2'),'price':Decimal('0.4'),'status':'CONFIRMED'},
           {'size':Decimal('1'),'price':Decimal('0.5'),'status':'CONFIRMED'}]
    result=settle(fills=fills)
    assert result['settlement_amount']=='3' and result['cost_basis']=='1.3' and result['realized_pnl']=='1.7'
    assert result['covered_shares']=='3' and result['confirmed_fill_count']==2 and result['cost_verified'] is True
    for partial in (fills[:1],[{**fills[0],'status':'PENDING'},fills[1]]):
        assert settle(fills=partial)['cost_basis'] is None and settle(fills=partial)['cost_verified'] is False
    assert settle(fills=fills,market_outcome_won=False,result_outcome_won=False)['realized_pnl']=='-1.3'


def test_settlement_refuses_sell_and_missing_shares():
    assert settle(side='SELL')['settlement_amount'] is None and '卖出' in settle(side='SELL')['reason']
    assert settle(matched_size=Decimal('0'))['settlement_amount'] is None
    assert settle(matched_size=None)['settlement_amount'] is None
