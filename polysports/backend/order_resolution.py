"""只读核对单场订单结算：赛果判断与 Polymarket 终态结算两个独立结果交叉印证。

两个结果分别是：由已绑定、已完赛的外部比分按市场命题规则推算的胜负（赛果结果），
以及 Polymarket data-api 终态二元赔付给出的胜负（市场结算结果）。理论上两者一致；
只有一致时才确认应结算金额，冲突时并列展示两个结果并要求人工核实，不掩盖差异。

赛果判断覆盖系统实际接受的三类全场市场：胜负（moneyline）、大小分/总进球
（totals）、以及选项明确写出让分数的让分盘（spreads）。半场、局节、单队、
球员和盘口口径不明确的市场一律不推断，返回 None 与具体原因。

市场结算读取官方 data-api `/v2/resolutions`：payouts 为 micro-USDC/份
（1 pUSD = 1_000_000），兼容旧式 0/1；只有终态 status 加可用的二元赔付才确认。
赛事终态（比赛结束）与市场终态（链上结算）是两个独立状态，不可互相替代。
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation, localcontext

import httpx

from .gamma import normal
from .sports_sources import SourceError

# 与 live_markets.supported_full_game_market 同一口径：局部市场不参与比分推断。
_PARTIAL_MARKET = re.compile(r'\b(?:first|second|1st|2nd|1h|2h|half|quarter|period|q[1-4]|team\s+total)\b', re.IGNORECASE)
# 保留小数点与正负号的题目规范化：normal() 会把 227.5 变成 "227 5"，不能用于解析盘口。
_TOTAL_LINE = re.compile(
    r'(?:\bo\s*/?\s*u\b|\bover\s*/?\s*under\b|\bover\s+under\b|\btotals?\b)'
    r'(?:\s+(?:points?|goals?|score|pts))?\s*[:=]?\s*(\d{1,4}(?:\.\d{1,2})?)'
)
_PROP_LINE = re.compile(r'\b(over|under)\s+(\d{1,4}(?:\.\d{1,2})?)\b')
_TOTAL_KEYWORD = re.compile(r'\bo\s*/?\s*u\b|\bover\b|\bunder\b|\btotals?\b')
_SPREAD_KEYWORD = re.compile(r'\b(?:spread|handicap)\b')
_SIDE_LABEL = re.compile(r'^(over|under|o|u)(?:\s+(\d{1,4}(?:\.\d{1,2})?))?$')
_SPREAD_LABEL = re.compile(r'^(.*?)\s*([+-]\d{1,3}(?:\.\d{1,2})?)$')
# 无队名的纯盘口题目允许的普通词；出现任何其他词（球员、其他球队等）即拒绝推断。
_GENERIC_WORDS = frozenset(
    {
        'o',
        'u',
        'v',
        'vs',
        'over',
        'under',
        'total',
        'totals',
        'point',
        'points',
        'goal',
        'goals',
        'score',
        'pts',
        'will',
        'there',
        'be',
        'in',
        'the',
        'a',
        'an',
        'game',
        'match',
        'between',
        'and',
        'on',
        'at',
        'of',
        'to',
        'for',
        'is',
        'it',
        'this',
        'both',
        'teams',
        'combined',
        'more',
        'than',
        'less',
        'exactly',
        'by',
        'line',
        'full',
        'time',
        'regulation',
        'or',
        'spread',
        'handicap',
        'yes',
        'no',
    }
)


def _compact(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _has_name(text, name):
    """完整队名或队名末词命中：Gamma 常用 "Cleveland Cavaliers"，题目只写 "Cavaliers"。"""
    if re.search(r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])', text):
        return True
    tail = name.split()[-1]
    return (
        len(tail) >= 4
        and tail not in _GENERIC_WORDS
        and bool(re.search(r'(?<![a-z0-9])' + re.escape(tail) + r'(?![a-z0-9])', text))
    )


def _team_in(text, h, a):
    for key, name in (('home', h), ('away', a)):
        if _has_name(text, name):
            return key
    return None


def _game_scope_ok(question, h, a):
    """题目须指向本场比赛；无队名的纯盘口题目也不得夹带其他主体。"""
    names = normal(question)
    if _has_name(names, h) and _has_name(names, a):
        return True
    words = re.findall(r'[a-z]+', _compact(question))
    return all(word in _GENERIC_WORDS for word in words)


def _number(value, *, allow_negative=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or abs(number) >= 10000:
        return None
    return number if allow_negative or number > 0 else None


def _judge_totals(*, q, label, question, h, a, home_score, away_score):
    """全场大小分/总进球：总分与盘口比较；正好相等视为走盘，不推断。"""
    if not _game_scope_ok(question, h, a):
        return None
    prop = _PROP_LINE.search(q)
    ql = _TOTAL_LINE.search(q)
    question_line = _number(ql.group(1)) if ql else (_number(prop.group(2)) if prop else None)
    side_match = _SIDE_LABEL.fullmatch(label)
    if side_match:
        side = 'over' if side_match.group(1) in ('over', 'o') else 'under'
        label_line = _number(side_match.group(2)) if side_match.group(2) else None
    elif label in ('yes', 'no') and prop:
        side = 'over' if (label == 'yes') == (prop.group(1) == 'over') else 'under'
        label_line = None
    else:
        return None
    if question_line and label_line and question_line != label_line:
        return None
    line = question_line or label_line or (_number(prop.group(2)) if prop else None)
    if line is None:
        return None
    total = Decimal(home_score + away_score)
    if total == line:
        return (None, f'全场总分 {total} 正好等于盘口 {line}，走盘规则无法自动判定')
    won = total > line if side == 'over' else total < line
    rule = '高于' if total > line else '低于'
    return (
        won,
        f'全场总分 {home_score}+{away_score}={total} {rule} 盘口 {line}，'
        f'{"Over" if side == "over" else "Under"}{"获胜" if won else "未获胜"}',
    )


def _judge_spread(*, label, question, h, a, home_score, away_score):
    """仅在选项自身写明「球队 + 让分数」时推断；Yes/No 等口径不明的不猜。"""
    if not _game_scope_ok(question, h, a):
        return None
    matched = _SPREAD_LABEL.fullmatch(label)
    if not matched:
        return None
    team = _team_in(normal(matched.group(1)), h, a)
    handicap = _number(matched.group(2), allow_negative=True)
    if team is None or handicap is None:
        return None
    own, other = (home_score, away_score) if team == 'home' else (away_score, home_score)
    adjusted = Decimal(own - other) + handicap
    if adjusted == 0:
        return (None, f'让分 {handicap:+} 后正好打平，走盘规则无法自动判定')
    won = adjusted > 0
    return (won, f'{matched.group(1).strip()} {own}:{other} 让分 {handicap:+} 后 {"覆盖" if won else "未覆盖"}盘口')


def judge_score_detail(*, sport, question, outcome_label, home, away, home_score, away_score, provider_status):
    """返回 (是否获胜|None, 依据说明)；规则不可严格识别时一律 None，附具体原因。"""
    if not all(isinstance(x, str) and x.strip() for x in (question, outcome_label, home, away)):
        return (None, '市场命题或已确认比分信息不足，不从比分推断输赢')
    if type(home_score) is not int or type(away_score) is not int:
        return (None, '已确认比分不是整数，不从比分推断输赢')
    if sport not in ('nba', 'football'):
        return (None, '运动类型不支持按比分自动判断')
    h, a = normal(home), normal(away)
    if not h or not a or h == a:
        return (None, '球队名称无法核实，不从比分推断输赢')
    q = _compact(question)
    label = _compact(outcome_label)
    qnames = normal(question)
    if _PARTIAL_MARKET.search(q.replace('-', ' ')):
        return (None, '半场、局节、单队或球员等局部市场不按全场比分推断')
    if sport == 'football' and provider_status != 'FT':
        return (None, '足球需常规时间完赛（FT）才按比分推断；加时、点球或状态未确认不推断')
    totals_keyword = bool(_TOTAL_KEYWORD.search(q))
    spread_keyword = bool(_SPREAD_KEYWORD.search(q))
    # 胜负命题（moneyline）：题目不带盘口关键词，选项为球队名或 Yes/No。
    if not totals_keyword and not spread_keyword:
        if sport == 'nba' and ' vs ' in f' {qnames} ' and _game_scope_ok(question, h, a):
            pick = _team_in(label, h, a)
            if pick and home_score != away_score:
                won = (home_score > away_score) if pick == 'home' else (away_score > home_score)
                return (
                    won,
                    f'胜负命题：{home} {home_score}:{away_score} {away}，'
                    f'{outcome_label.strip()} {"获胜" if won else "未获胜"}',
                )
        if sport == 'football':
            matched = re.fullmatch(r'will (.+) win on \d{4} \d{2} \d{2}', qnames)
            picked = _team_in(matched.group(1), h, a) if matched else None
            if picked and label in ('yes', 'no'):
                team_won = (home_score > away_score) if picked == 'home' else (away_score > home_score)
                won = team_won if label == 'yes' else not team_won
                return (
                    won,
                    f'球队获胜命题：{home} {home_score}:{away_score} {away}，'
                    f'{outcome_label.strip()} {"获胜" if won else "未获胜"}',
                )
        return (None, '市场命题规则无法严格识别，不从比分推断输赢')
    if totals_keyword:
        totals = _judge_totals(
            q=q, label=label, question=question, h=h, a=a, home_score=home_score, away_score=away_score
        )
        if totals is not None:
            return totals
    if spread_keyword:
        spread = _judge_spread(label=label, question=question, h=h, a=a, home_score=home_score, away_score=away_score)
        if spread is not None:
            return spread
        return (None, '让分/让球盘口口径未明确写在选项中，需人工核实或等待市场终态')
    return (None, '大小分盘口或选项无法严格识别，需人工核实或等待市场终态')


def judge_score(**kwargs):
    """按已确认比分独立判断所选选项是否获胜；无法严格识别返回 None。"""
    return judge_score_detail(**kwargs)[0]


def compare_score(*, outcome_won, **kwargs):
    """以市场终态结果为基准比较比分推算结果；保留既有 score_consistency 字段口径。"""
    expected = judge_score(**kwargs)
    if expected is None or type(outcome_won) is not bool:
        return 'unsupported'
    return 'consistent' if expected == outcome_won else 'conflict'


# 兼容既有调用点与测试中的旧命名；语义已扩展到全部三类全场市场。
judge_moneyline_score = judge_score
compare_moneyline_score = compare_score


# data-api /v2/resolutions 的 payouts 单位为 micro-USDC/份：1 pUSD = 1_000_000。
# 旧版/部分来源仍可能返回 0/1，两种单位都接受；未知单位一律不猜。
_PAYOUT_SCALES = (Decimal(1), Decimal(1000000))
_TERMINAL_STATES = frozenset({'resolved', 'finalized', 'settled'})
_STAGE_TEXT = {
    'initialized': '结算流程已初始化，尚未提议结果',
    'posed': '已发起预言机提问，等待提议结果',
    'proposed': '已提议结果，处于无争议期，尚未最终结算',
    'challenged': '提议结果被挑战，等待复核',
    'reproposed': '已重新提议结果，等待无争议期',
    'disputed': '结果存在争议，等待仲裁',
    'active': '市场仍在交易，尚未发起最终结算',
    'arbitration': '结果处于仲裁中',
}


def _binary_payout_won(payouts, outcome_index):
    """把官方二元终态赔付归一化为「所选选项是否获胜」；无法严格识别返回 None。"""
    if not isinstance(payouts, (list, tuple)) or len(payouts) != 2:
        return None
    values = []
    for raw in payouts:
        if isinstance(raw, bool):
            return None
        if type(raw) is int or isinstance(raw, str) and re.fullmatch(r'\d{1,12}', raw):
            value = Decimal(raw)
        elif type(raw) is float and raw.is_integer() and abs(raw) < 10**12:
            value = Decimal(str(raw))
        else:
            return None
        if not value.is_finite() or value < 0:
            return None
        values.append(value)
    # 恰好一个选项全额获胜、另一个为 0；split（50/50）、分数赔付与 0/0 都不猜。
    winners = [i for i, value in enumerate(values) if value > 0]
    if len(winners) != 1 or values[winners[0]] not in _PAYOUT_SCALES:
        return None
    return winners[0] == outcome_index


def _pending_resolution(status, text):
    return {'state': 'pending', 'market_status': status, 'market_status_text': text}


def read_resolution(client, condition_id, outcome_index):
    """只读查询 Polymarket data-api 终态结算；非终态也返回具体阶段，不再笼统标为「未终态」。

    官方响应示例：status="resolved"、payouts=[1000000,0]、resolved_at=RFC3339。
    condition/event 选择器可能返回多行（V2 生命周期 → UMA 生命周期 → CTF 终态），
    因此按「终态且赔付可用」选行，并要求同一查询内不能出现互相矛盾的终态结果。
    """
    if not isinstance(condition_id, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', condition_id):
        raise SourceError('invalid_condition_id')
    if type(outcome_index) is not int or outcome_index not in (0, 1):
        raise SourceError('invalid_outcome_index')
    try:
        response = client.get(
            'https://data-api.polymarket.com/v2/resolutions',
            params={'condition': condition_id},
            timeout=15,
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise SourceError('http_' + str(response.status_code))
        body = response.json()
    except httpx.TimeoutException as exc:
        raise SourceError('timeout') from exc
    except (httpx.RequestError, ValueError) as exc:
        raise SourceError('network_or_invalid_json') from exc
    if not isinstance(body, dict) or not isinstance(body.get('data'), list):
        raise SourceError('invalid_response')
    rows = body['data']
    if not rows:
        return _pending_resolution(
            'not_found', '结算数据暂未收录该市场条件（该盘口尚未发起结算，或条件编号与记录不同）'
        )
    matched = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get('condition_id', '')).lower() == condition_id.lower()
    ]
    if not matched:
        raise SourceError('resolution_identity_mismatch')
    terminal = []
    for row in matched:
        status = str(row.get('status', '')).lower()
        if status not in _TERMINAL_STATES:
            continue
        won = _binary_payout_won(row.get('payouts'), outcome_index)
        if won is not None:
            terminal.append((row, status, won))
    if terminal:
        if len({won for _, _, won in terminal}) > 1:
            raise SourceError('resolution_conflict')
        row, status, won = terminal[0]
        return {
            'state': 'resolved',
            'outcome_won': won,
            'resolved_at': row.get('resolved_at'),
            'market_status': status,
            'market_status_text': '已最终结算',
            'payouts': row.get('payouts'),
            'reporter': row.get('reporter'),
        }
    statuses = [str(row.get('status', '')).lower() for row in matched]
    already_terminal = next((status for status in statuses if status in _TERMINAL_STATES), None)
    if already_terminal:
        return _pending_resolution(already_terminal, '已标记结算，但未返回可用的二元赔付明细，暂不确认金额')
    stage = next((status for status in statuses if status), 'unknown')
    return _pending_resolution(stage, _STAGE_TEXT.get(stage, '结算流程尚未完成'))


def settlement_amounts(*, side, matched_size, fills, market_outcome_won, result_outcome_won):
    """并列核算「赛果判断」与「Polymarket 终态结算」两个结果，互相印证一致才确认金额。

    两个结果各自按二元终态赔付（获胜每份 1 pUSD、未获胜 0）与已确认成交份额换算。
    agreement 取值：consistent（两结果一致，确认金额）、conflict（不一致，保留两个
    金额但不确认）、market_only（仅有市场结果）、result_only（仅有赛果结果，金额为
    预计值不确认）、unavailable（两者皆无）。成本与盈亏只在金额已确认且 CONFIRMED
    成交明细完整覆盖已成交份额时给出，绝不用限价猜测成交价。
    """
    result = {
        'settlement_amount': None,
        'market_settlement_amount': None,
        'result_settlement_amount': None,
        'market_outcome_won': None,
        'result_outcome_won': None,
        'agreement': 'unavailable',
        'cost_basis': None,
        'realized_pnl': None,
        'covered_shares': None,
        'confirmed_fill_count': 0,
        'cost_verified': False,
        'currency': 'pUSD',
        'reason': None,
        'notice': '按市场终态赔付与已确认成交份额计算，不含未记录的费用；不等于链上赎回或实际到账。',
    }
    if side != 'BUY':
        result['reason'] = '卖出订单按成交价结算，不适用市场最终赔付'
        return result
    try:
        shares = Decimal(str(matched_size))
    except (InvalidOperation, TypeError, ValueError):
        shares = Decimal(0)
    with localcontext() as context:
        context.prec = 90
        if not shares.is_finite() or shares <= 0:
            result['reason'] = '订单没有已确认的成交份额'
            return result
        market_won = market_outcome_won if type(market_outcome_won) is bool else None
        result_won = result_outcome_won if type(result_outcome_won) is bool else None
        result['market_outcome_won'] = market_won
        result['result_outcome_won'] = result_won

        def amount(won):
            return format(shares if won else Decimal(0), 'f')

        if market_won is not None:
            result['market_settlement_amount'] = amount(market_won)
        if result_won is not None:
            result['result_settlement_amount'] = amount(result_won)
        if market_won is not None and result_won is not None:
            if market_won == result_won:
                result['agreement'] = 'consistent'
                result['settlement_amount'] = result['market_settlement_amount']
                result['notice'] = '赛果判断与 Polymarket 终态结算一致；' + result['notice']
            else:
                result['agreement'] = 'conflict'
                result['reason'] = '两个结算结果不一致，需人工核实，不确认结算金额'
        elif market_won is not None:
            result['agreement'] = 'market_only'
            result['settlement_amount'] = result['market_settlement_amount']
            result['notice'] += '该市场规则无法从比分自动判断或赛果未确认，以市场终态结算为准，建议人工核实。'
        elif result_won is not None:
            result['agreement'] = 'result_only'
            result['reason'] = 'Polymarket 尚未返回终态结算；按赛果推算的金额仅为预计值，待市场结算确认'
            result['notice'] += '按赛果推算的金额不是已确认结算。'
        else:
            result['reason'] = '赛果规则无法自动判断且市场尚未终态，暂不能确认结算金额'
        confirmed = [x for x in fills if str(x.get('status', '')).upper() == 'CONFIRMED']
        result['confirmed_fill_count'] = len(confirmed)
        total = Decimal(0)
        cost = Decimal(0)
        valid = True
        for item in confirmed:
            try:
                size = Decimal(str(item['size']))
                price = Decimal(str(item['price']))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                valid = False
                break
            if not size.is_finite() or size <= 0 or not price.is_finite() or not 0 < price < 1:
                valid = False
                break
            total += size
            cost += size * price
        if valid and total == shares:
            result['covered_shares'] = format(total, 'f')
            if result['settlement_amount'] is not None:
                result['cost_basis'] = format(cost, 'f')
                result['realized_pnl'] = format(Decimal(result['settlement_amount']) - cost, 'f')
                result['cost_verified'] = True
        elif result['settlement_amount'] is not None:
            result['reason'] = '已确认成交明细未覆盖全部成交份额，成本与盈亏待核实（可先查询成交记录）'
    return result
