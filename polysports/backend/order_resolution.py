"""只读核对单场订单结算：赛果判断与 Polymarket 终态结算两个独立结果交叉印证。

两个结果分别是：由已绑定、已完赛的外部比分按市场命题规则推算的胜负（赛果结果），
以及 Polymarket data-api 终态二元赔付给出的胜负（市场结算结果）。理论上两者一致；
只有一致时才确认应结算金额，冲突时并列展示两个结果并要求人工核实，不掩盖差异。
"""
import re
from decimal import Decimal, InvalidOperation, localcontext

import httpx

from .sports_sources import SourceError
from .gamma import normal


def judge_moneyline_score(*,sport,question,outcome_label,home,away,home_score,away_score,provider_status):
    """只依据已确认比分与市场命题判断所选选项是否获胜；规则不可严格识别时返回 None。"""
    if not all(isinstance(x,str) and x.strip() for x in (question,outcome_label,home,away)):
        return None
    if type(home_score) is not int or type(away_score) is not int:
        return None
    h,a=normal(home),normal(away)
    if not h or not a or h==a: return None
    q=normal(question);label=normal(outcome_label)
    if sport=='nba':
        if q not in (f'{h} vs {a}',f'{a} vs {h}') or label not in (h,a) or home_score==away_score:
            return None
        return (home_score>away_score) if label==h else (away_score>home_score)
    if sport=='football' and provider_status=='FT':
        # 仅接受明确的球队获胜命题；加时/点球及其他问题保持待核实。
        matched=re.fullmatch(r'will (.+) win on (\d{4}) (\d{2}) (\d{2})',q)
        if not matched or matched.group(1) not in (h,a) or label not in ('yes','no'):
            return None
        team_won=home_score>away_score if matched.group(1)==h else away_score>home_score
        return team_won if label=='yes' else not team_won
    return None


def compare_moneyline_score(*,sport,question,outcome_label,home,away,home_score,away_score,provider_status,outcome_won):
    """以市场终态结果为基准比较比分推算结果；保留既有 score_consistency 字段口径。"""
    expected=judge_moneyline_score(sport=sport,question=question,outcome_label=outcome_label,home=home,away=away,
                                   home_score=home_score,away_score=away_score,provider_status=provider_status)
    if expected is None or type(outcome_won) is not bool: return 'unsupported'
    return 'consistent' if expected==outcome_won else 'conflict'


def read_resolution(client, condition_id, outcome_index):
    if not isinstance(condition_id,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',condition_id):
        raise SourceError('invalid_condition_id')
    if type(outcome_index) is not int or outcome_index not in (0,1): raise SourceError('invalid_outcome_index')
    try:
        response=client.get('https://data-api.polymarket.com/v2/resolutions',params={'condition':condition_id},timeout=15,follow_redirects=False)
        if response.status_code!=200: raise SourceError('http_'+str(response.status_code))
        body=response.json()
    except httpx.TimeoutException as exc: raise SourceError('timeout') from exc
    except (httpx.RequestError,ValueError) as exc: raise SourceError('network_or_invalid_json') from exc
    if not isinstance(body,dict) or not isinstance(body.get('data'),list): raise SourceError('invalid_response')
    rows=body['data']
    if not rows: return {'state':'pending'}
    if len(rows)!=1 or not isinstance(rows[0],dict) or str(rows[0].get('condition_id','')).lower()!=condition_id.lower():
        raise SourceError('resolution_identity_mismatch')
    row=rows[0];payouts=row.get('payouts')
    # 非终态、分数赔付或特殊结算不得猜测结果。
    if str(row.get('status','')).lower() not in ('resolved','finalized','settled') or not row.get('resolved_at') or not isinstance(payouts,list) or len(payouts)!=2 or any(type(x) is not int for x in payouts) or payouts not in ([0,1],[1,0]):
        return {'state':'pending'}
    return {'state':'resolved','outcome_won':payouts[outcome_index]==1,'resolved_at':row['resolved_at']}


def settlement_amounts(*,side,matched_size,fills,market_outcome_won,result_outcome_won):
    """并列核算「赛果判断」与「Polymarket 终态结算」两个结果，互相印证一致才确认金额。

    两个结果各自按二元终态赔付（获胜每份 1 pUSD、未获胜 0）与已确认成交份额换算。
    agreement 取值：consistent（两结果一致，确认金额）、conflict（不一致，保留两个
    金额但不确认）、market_only（仅有市场结果）、result_only（仅有赛果结果，金额为
    预计值不确认）、unavailable（两者皆无）。成本与盈亏只在金额已确认且 CONFIRMED
    成交明细完整覆盖已成交份额时给出，绝不用限价猜测成交价。
    """
    result={'settlement_amount':None,'market_settlement_amount':None,'result_settlement_amount':None,
            'market_outcome_won':None,'result_outcome_won':None,'agreement':'unavailable',
            'cost_basis':None,'realized_pnl':None,'covered_shares':None,
            'confirmed_fill_count':0,'cost_verified':False,'currency':'pUSD','reason':None,
            'notice':'按市场终态赔付与已确认成交份额计算，不含未记录的费用；不等于链上赎回或实际到账。'}
    if side!='BUY':
        result['reason']='卖出订单按成交价结算，不适用市场最终赔付'
        return result
    try:
        shares=Decimal(str(matched_size))
    except (InvalidOperation,TypeError,ValueError):
        shares=Decimal(0)
    with localcontext() as context:
        context.prec=90
        if not shares.is_finite() or shares<=0:
            result['reason']='订单没有已确认的成交份额'
            return result
        market_won=market_outcome_won if type(market_outcome_won) is bool else None
        result_won=result_outcome_won if type(result_outcome_won) is bool else None
        result['market_outcome_won']=market_won
        result['result_outcome_won']=result_won
        def amount(won): return format(shares if won else Decimal(0),'f')
        if market_won is not None: result['market_settlement_amount']=amount(market_won)
        if result_won is not None: result['result_settlement_amount']=amount(result_won)
        if market_won is not None and result_won is not None:
            if market_won==result_won:
                result['agreement']='consistent'
                result['settlement_amount']=result['market_settlement_amount']
                result['notice']='赛果判断与 Polymarket 终态结算一致；'+result['notice']
            else:
                result['agreement']='conflict'
                result['reason']='两个结算结果不一致，需人工核实，不确认结算金额'
        elif market_won is not None:
            result['agreement']='market_only'
            result['settlement_amount']=result['market_settlement_amount']
            result['notice']+='该市场规则无法从比分自动判断或赛果未确认，以市场终态结算为准，建议人工核实。'
        elif result_won is not None:
            result['agreement']='result_only'
            result['reason']='市场尚未进入终态；按赛果推算的金额仅为预计值，待 Polymarket 结算确认'
            result['notice']+='按赛果推算的金额不是已确认结算。'
        else:
            result['reason']='赛果规则无法自动判断且市场尚未终态，暂不能确认结算金额'
        confirmed=[x for x in fills if str(x.get('status','')).upper()=='CONFIRMED']
        result['confirmed_fill_count']=len(confirmed)
        total=Decimal(0);cost=Decimal(0);valid=True
        for item in confirmed:
            try:
                size=Decimal(str(item['size']));price=Decimal(str(item['price']))
            except (KeyError,InvalidOperation,TypeError,ValueError):
                valid=False;break
            if not size.is_finite() or size<=0 or not price.is_finite() or not 0<price<1:
                valid=False;break
            total+=size;cost+=size*price
        if valid and total==shares:
            result['covered_shares']=format(total,'f')
            if result['settlement_amount'] is not None:
                result['cost_basis']=format(cost,'f')
                result['realized_pnl']=format(Decimal(result['settlement_amount'])-cost,'f')
                result['cost_verified']=True
        elif result['settlement_amount'] is not None:
            result['reason']='已确认成交明细未覆盖全部成交份额，成本与盈亏待核实（可先查询成交记录）'
    return result
