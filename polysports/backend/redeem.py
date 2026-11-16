"""已结算仓位的可赎回资格与整仓赎回编排。"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation, localcontext

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import AuditEvent, ComboRFQ, MarketLink, MarketOutcome, PolyMarket, RedeemRequest, TradeIntent
from .redeem_gateway import RedeemGateway, RedeemUnknown
from .scheduler import db_now
from .trading import canonical, wallet_for
from .trading_gateway import TradingError

SINGLE_POSITIONS_URL = 'https://data-api.polymarket.com/v2/positions'
COMBO_POSITIONS_URL = 'https://data-api.polymarket.com/v2/positions/combos'
MAX_ITEMS = 100


def _size(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _transaction_evidence(tx_hash, transaction_id):
    value = tx_hash.lower() if isinstance(tx_hash, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', tx_hash) else None
    ident = str(transaction_id)[:128] if transaction_id is not None and str(transaction_id) else None
    return value, ident


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    return text if text.isascii() and text.isdigit() and len(text) <= 78 else None


def _chunks(values, size=20):
    unique = list(dict.fromkeys(values))
    for start in range(0, len(unique), size):
        yield unique[start:start + size]


def _pages(client, url, params):
    cursor = None
    seen = set()
    for _ in range(20):
        query = dict(params)
        if cursor is not None:
            query['cursor'] = cursor
        try:
            response = client.get(url, params=query, timeout=10.0, follow_redirects=False)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise TradingError('redeem_eligibility_unavailable', '可赎回仓位查询暂不可用') from exc
        if response.status_code != 200:
            raise TradingError('redeem_eligibility_unavailable', '可赎回仓位查询暂不可用')
        try:
            body = response.json()
        except ValueError as exc:
            raise TradingError('redeem_eligibility_invalid', '可赎回仓位响应无效') from exc
        if not isinstance(body, dict):
            raise TradingError('redeem_eligibility_invalid', '可赎回仓位响应无效')
        rows = body.get('data')
        pagination = body.get('pagination')
        if not isinstance(rows, list) or not isinstance(pagination, dict):
            raise TradingError('redeem_eligibility_invalid', '可赎回仓位响应无效')
        next_cursor = pagination.get('next_cursor')
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise TradingError('redeem_eligibility_invalid', '可赎回仓位分页无效')
        if 'has_more' in pagination and (type(pagination['has_more']) is not bool or pagination['has_more'] != bool(next_cursor)):
            raise TradingError('redeem_eligibility_invalid', '可赎回仓位分页无效')
        yield rows
        if next_cursor is None:
            return
        if next_cursor in seen:
            raise TradingError('redeem_eligibility_invalid', '可赎回仓位分页重复')
        seen.add(next_cursor)
        cursor = next_cursor
    raise TradingError('redeem_eligibility_invalid', '可赎回仓位分页过多')


def fetch_redeemable_single_positions(client, wallet_address, condition_ids=None):
    result = {}
    conditions = list(dict.fromkeys(x.lower() for x in (condition_ids or []) if isinstance(x, str)))
    for chunk in _chunks(conditions or ['']):
        params = {'user': wallet_address.lower(), 'status': 'REDEEMABLE', 'limit': 1000,
                  'filter_amount': 0, 'include_archived': True}
        if chunk and chunk != ['']:
            params['condition'] = ','.join(chunk)
        for rows in _pages(client, SINGLE_POSITIONS_URL, params):
            for row in rows:
                if not isinstance(row, dict) or row.get('redeemable') is not True:
                    continue
                token_id = _identifier(row.get('token_id'))
                shares = _size(row.get('current_size'))
                if token_id is None or shares is None:
                    continue
                negative_risk = row.get('negative_risk')
                result[token_id] = {
                    'shares': shares,
                    'condition_id': str(row.get('condition_id', '')).lower() or None,
                    'outcome_index': row.get('outcome_index') if type(row.get('outcome_index')) is int else None,
                    'negative_risk': negative_risk if type(negative_risk) is bool else None,
                }
    return result


def fetch_redeemable_combo_positions(client, wallet_address):
    result = {}
    params = {'user': wallet_address.lower(), 'status': 'REDEEMABLE', 'limit': 1000}
    for rows in _pages(client, COMBO_POSITIONS_URL, params):
        for row in rows:
            if not isinstance(row, dict) or row.get('redeemable') is not True:
                continue
            position_id = _identifier(row.get('combo_position_id'))
            shares = _size(row.get('current_size'))
            if position_id is None or shares is None:
                continue
            result[position_id] = {'shares': shares}
    return result


def _single_candidate(db, settings, intent):
    if intent is None:
        return None, '订单不存在'
    if intent.side != 'BUY':
        return None, '只有买入仓位可以赎回'
    if intent.matched_size is None or intent.matched_size <= 0:
        return None, '订单没有已确认的买入份额'
    try:
        wallet = wallet_for(settings, intent.wallet_id)
    except TradingError as exc:
        return None, exc.message
    if wallet.wallet_address != intent.wallet_address:
        return None, '订单钱包已变化'
    link = db.get(MarketLink, intent.market_link_id)
    outcome = db.get(MarketOutcome, intent.outcome_id) if link else None
    market = db.get(PolyMarket, link.market_id) if link else None
    if not link or not outcome or not market or link.game_id != intent.game_id or outcome.market_id != market.id:
        return None, '订单与市场身份不一致'
    if outcome.asset_kind not in ('position', 'ctf_token'):
        return None, '仓位资产类型无法确认'
    condition_id = market.condition_id.lower()
    return {
        'kind': 'single', 'id': intent.id, 'wallet_id': intent.wallet_id,
        'wallet_address': wallet.wallet_address, 'target_ref': condition_id,
        'condition_id': condition_id, 'outcome_index': outcome.outcome_index,
        'asset_id': outcome.asset_id, 'asset_kind': outcome.asset_kind,
        'intent_id': intent.id, 'combo_rfq_id': None, 'position_id': None,
    }, None


def _combo_candidate(db, settings, row):
    if row is None:
        return None, '组合记录不存在'
    if row.direction != 'BUY':
        return None, '只有买入组合可以赎回'
    if row.state != 'confirmed':
        return None, '组合尚未确认成交'
    position_id = _identifier(row.combo_position_id)
    if position_id is None:
        return None, '组合缺少可核实的持仓标识'
    try:
        wallet = wallet_for(settings, row.wallet_id)
    except TradingError as exc:
        return None, exc.message
    if wallet.wallet_address != row.wallet_address:
        return None, '组合钱包已变化'
    return {
        'kind': 'combo', 'id': row.id, 'wallet_id': row.wallet_id,
        'wallet_address': wallet.wallet_address, 'target_ref': position_id,
        'condition_id': None, 'outcome_index': None, 'asset_id': None,
        'asset_kind': None, 'intent_id': None, 'combo_rfq_id': row.id,
        'position_id': position_id,
    }, None


def _existing_map(db, wallet_ids):
    if not wallet_ids:
        return {}
    rows = db.scalars(select(RedeemRequest).where(RedeemRequest.wallet_id.in_(wallet_ids)))
    return {(str(x.wallet_id), x.target_type, x.target_ref): x for x in rows}


def eligibility(engine, settings: Settings, intent_ids, combo_ids, client=None):
    intent_ids = list(dict.fromkeys(intent_ids or []))
    combo_ids = list(dict.fromkeys(combo_ids or []))
    if len(intent_ids) > MAX_ITEMS or len(combo_ids) > MAX_ITEMS:
        raise TradingError('invalid_amount', '资格查询数量超过限制')
    cards = []
    targets = {}
    with Session(engine) as db:
        intents = {x.id: x for x in db.scalars(select(TradeIntent).where(TradeIntent.id.in_(intent_ids)))} if intent_ids else {}
        combos = {x.id: x for x in db.scalars(select(ComboRFQ).where(ComboRFQ.id.in_(combo_ids)))} if combo_ids else {}
        for ident in intent_ids:
            target, reason = _single_candidate(db, settings, intents.get(ident))
            card = {'kind': 'single', 'id': str(ident), 'eligible': False, 'state': None,
                    'shares': None, 'expected_payout': None, 'reason': reason}
            cards.append(card)
            if target is not None:
                targets[('single', str(ident))] = target
        for ident in combo_ids:
            target, reason = _combo_candidate(db, settings, combos.get(ident))
            card = {'kind': 'combo', 'id': str(ident), 'eligible': False, 'state': None,
                    'shares': None, 'expected_payout': None, 'reason': reason}
            cards.append(card)
            if target is not None:
                targets[('combo', str(ident))] = target
        wallet_ids = sorted({target['wallet_id'] for target in targets.values()}, key=str)
        existing = _existing_map(db, wallet_ids)

    execution_enabled = bool(settings.redeem_execution_enabled)
    snapshots = {}
    if execution_enabled and targets:
        requested = {}
        for target in targets.values():
            bucket = requested.setdefault(target['wallet_id'], {'address': target['wallet_address'], 'conditions': [], 'combo': False})
            if target['kind'] == 'single':
                bucket['conditions'].append(target['condition_id'])
            else:
                bucket['combo'] = True
        owns_client = client is None
        source = client or httpx.Client()
        try:
            for wallet_id, bucket in requested.items():
                state = {'singles': {}, 'combos': {}, 'single_error': None, 'combo_error': None}
                if bucket['conditions']:
                    try:
                        state['singles'] = fetch_redeemable_single_positions(source, bucket['address'], bucket['conditions'])
                    except TradingError as exc:
                        state['single_error'] = exc.message
                if bucket['combo']:
                    try:
                        state['combos'] = fetch_redeemable_combo_positions(source, bucket['address'])
                    except TradingError as exc:
                        state['combo_error'] = exc.message
                snapshots[wallet_id] = state
        finally:
            if owns_client:
                source.close()

    for card in cards:
        key = (card['kind'], card['id'])
        target = targets.get(key)
        if target is None:
            continue
        existing_row = existing.get((str(target['wallet_id']), target['kind'], target['target_ref']))
        if existing_row is not None:
            card['state'] = existing_row.state
            card['shares'] = canonical(existing_row.shares)
            card['expected_payout'] = canonical(existing_row.payout) if existing_row.payout is not None else None
            if existing_row.state == 'confirmed':
                card['reason'] = '该整仓已经赎回'
                continue
            if existing_row.state in ('submitting', 'unknown'):
                card['reason'] = '赎回请求正在进行或结果未知，禁止重复操作'
                continue
            card['reason'] = existing_row.error_code
        if not execution_enabled:
            card['reason'] = '赎回功能未开启'
            continue
        snapshot = snapshots.get(target['wallet_id'], {})
        if target['kind'] == 'single':
            if snapshot.get('single_error'):
                card['reason'] = snapshot['single_error']
                continue
            position = snapshot.get('singles', {}).get(target['asset_id'])
            if position is None:
                card['reason'] = '当前没有可赎回的赢家份额'
                continue
            if target['asset_kind'] == 'ctf_token' and type(position.get('negative_risk')) is not bool:
                card['reason'] = '无法确认 CTF 市场类型和赎回授权'
                continue
            target['negative_risk'] = position.get('negative_risk')
        else:
            if snapshot.get('combo_error'):
                card['reason'] = snapshot['combo_error']
                continue
            position = snapshot.get('combos', {}).get(target['position_id'])
            if position is None:
                card['reason'] = '当前没有可赎回的组合赢家份额'
                continue
        card.update({'eligible': True, 'shares': canonical(position['shares']),
                     'expected_payout': canonical(position['shares']), 'reason': None})
    return {'items': cards, 'execution_enabled': execution_enabled}


def _target_for_single(engine, settings, intent_id):
    with Session(engine) as db:
        target, reason = _single_candidate(db, settings, db.get(TradeIntent, intent_id))
    if target is None:
        raise TradingError('redeem_not_available', reason)
    return target


def _target_for_combo(engine, settings, combo_id):
    with Session(engine) as db:
        target, reason = _combo_candidate(db, settings, db.get(ComboRFQ, combo_id))
    if target is None:
        raise TradingError('redeem_not_available', reason)
    return target


def _current_position(target, settings, client):
    if target['kind'] == 'single':
        positions = fetch_redeemable_single_positions(client, target['wallet_address'], [target['condition_id']])
        position = positions.get(target['asset_id'])
        if position is None:
            raise TradingError('redeem_not_available', '当前没有可赎回的赢家份额')
        if target['asset_kind'] == 'ctf_token' and type(position.get('negative_risk')) is not bool:
            raise TradingError('redeem_approval_unknown', '无法确认 CTF 市场类型和赎回授权')
        target['negative_risk'] = position.get('negative_risk')
    else:
        position = fetch_redeemable_combo_positions(client, target['wallet_address']).get(target['position_id'])
        if position is None:
            raise TradingError('redeem_not_available', '当前没有可赎回的组合赢家份额')
    target['shares'] = position['shares']
    return target


def _existing_row(engine, target):
    with Session(engine) as db:
        return db.scalar(select(RedeemRequest).where(
            RedeemRequest.wallet_id == target['wallet_id'],
            RedeemRequest.target_type == target['kind'],
            RedeemRequest.target_ref == target['target_ref']))


def _redeem_data(row, already=False):
    credited = canonical(row.payout) if row.payout is not None else None
    message = {
        'confirmed': '赎回已确认，资金已进入现金余额',
        'submitting': '赎回请求正在处理',
        'rejected': '赎回请求未发送',
        'unknown': '赎回结果未知，请勿重复操作，请到 Polymarket 核对',
    }.get(row.state, '赎回状态未知')
    return {
        'id': str(row.id), 'kind': row.target_type, 'state': row.state,
        'shares': canonical(row.shares),
        'payout': canonical(row.payout) if row.payout is not None else None,
        'credited_amount': credited,
        'collateral_balance': canonical(row.collateral_balance) if row.collateral_balance is not None else None,
        'tx_hash': row.tx_hash, 'already_redeemed': already, 'message': message,
    }


def _begin(engine, target, request_id, actor_id, correlation_id):
    key = (target['wallet_id'], target['kind'], target['target_ref'])
    try:
        with Session(engine) as db, db.begin():
            row = db.scalar(select(RedeemRequest).where(
                RedeemRequest.wallet_id == key[0],
                RedeemRequest.target_type == key[1],
                RedeemRequest.target_ref == key[2]).with_for_update())
            now = db_now(db)
            if row is not None:
                if row.state == 'confirmed':
                    return str(row.id), False
                if row.state in ('submitting', 'unknown'):
                    raise TradingError('redeem_in_progress', '该整仓已有进行中或结果未知的赎回请求')
            else:
                row = RedeemRequest(id=uuid.uuid4(), wallet_id=target['wallet_id'],
                                    wallet_address=target['wallet_address'],
                                    target_type=target['kind'], target_ref=target['target_ref'],
                                    client_request_id=request_id, state='submitting',
                                    shares=target['shares'])
                db.add(row)
                db.flush()
            row.wallet_address = target['wallet_address']
            row.intent_id = target['intent_id']
            row.combo_rfq_id = target['combo_rfq_id']
            row.condition_id = target['condition_id']
            row.outcome_index = target['outcome_index']
            row.position_id = target['position_id']
            row.shares = target['shares']
            row.client_request_id = request_id
            row.state = 'submitting'
            row.tx_hash = None
            row.transaction_id = None
            row.payout = None
            row.collateral_before = None
            row.collateral_balance = None
            row.error_code = None
            row.confirmed_at = None
            row.updated_at = now
            db.add(AuditEvent(id=uuid.uuid4(), actor_id=actor_id, action='redeem_requested',
                              entity_type='redeem_request', entity_id=row.id,
                              correlation_id=correlation_id,
                              details={'kind': row.target_type, 'shares': canonical(row.shares)}))
            return str(row.id), True
    except IntegrityError as exc:
        existing = _existing_row(engine, target)
        if existing is not None and existing.state == 'confirmed':
            return str(existing.id), False
        raise TradingError('redeem_in_progress', '该整仓已有进行中的赎回请求') from exc


def _finish(engine, redemption_id, state, *, code=None, tx_hash=None, transaction_id=None,
            payout=None, collateral_before=None, collateral_balance=None,
            actor_id=None, correlation_id=None):
    with Session(engine) as db, db.begin():
        row = db.scalar(select(RedeemRequest).where(RedeemRequest.id == uuid.UUID(redemption_id)).with_for_update())
        if row is None or row.state not in ('submitting', 'unknown'):
            return _redeem_data(row) if row is not None else None
        now = db_now(db)
        row.state = state
        row.error_code = code
        row.tx_hash = tx_hash
        row.transaction_id = transaction_id
        row.payout = payout
        row.collateral_before = collateral_before
        row.collateral_balance = collateral_balance
        row.updated_at = now
        if state == 'confirmed':
            row.confirmed_at = now
        action = {'confirmed': 'redeem_confirmed', 'rejected': 'redeem_rejected',
                  'unknown': 'redeem_unknown'}.get(state, 'redeem_updated')
        db.add(AuditEvent(id=uuid.uuid4(), actor_id=actor_id, action=action,
                          entity_type='redeem_request', entity_id=row.id,
                          correlation_id=correlation_id,
                          details={'kind': row.target_type, 'state': state, 'error_code': code}))
        return _redeem_data(row)


def _wallet_balance(gateway):
    try:
        value = gateway.wallet_status().get('collateral_balance')
        return _size_or_zero(value)
    except (TradingError, AttributeError, TypeError, ValueError):
        return None


def _size_or_zero(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _execute(engine, settings, target, request_id, actor_id, correlation_id, gateway_cls):
    if not settings.redeem_execution_enabled:
        raise TradingError('redeem_disabled', '赎回功能未开启')
    old = _existing_row(engine, target)
    if old is not None:
        if old.state == 'confirmed':
            return _redeem_data(old, already=True), 200
        if old.state in ('submitting', 'unknown'):
            raise TradingError('redeem_in_progress', '该整仓已有进行中或结果未知的赎回请求')
    with httpx.Client() as client:
        target = _current_position(target, settings, client)

    wallet = wallet_for(settings, target['wallet_id'])
    with gateway_cls(settings, wallet) as gateway:
        gateway.preflight_approval(target_type=target['kind'], asset_kind=target['asset_kind'],
                                   negative_risk=target.get('negative_risk'))
        before = _wallet_balance(gateway)
        redemption_id, submit = _begin(engine, target, request_id, actor_id, correlation_id)
        if not submit:
            with Session(engine) as db:
                row = db.get(RedeemRequest, uuid.UUID(redemption_id))
                return _redeem_data(row, already=True), 200
        try:
            tx_hash, transaction_id = gateway.redeem(
                condition_id=target['condition_id'] if target['kind'] == 'single' else None,
                position_id=target['position_id'] if target['kind'] == 'combo' else None)
        except RedeemUnknown as exc:
            tx_hash, transaction_id = _transaction_evidence(exc.tx_hash, exc.transaction_id)
            data = _finish(engine, redemption_id, 'unknown', code='redeem_confirmation_unknown',
                           tx_hash=tx_hash, transaction_id=transaction_id, collateral_before=before,
                           actor_id=actor_id, correlation_id=correlation_id)
            return data, 202
        except TradingError as exc:
            data = _finish(engine, redemption_id, 'rejected', code=exc.code,
                           collateral_before=before, actor_id=actor_id, correlation_id=correlation_id)
            return data, 409
        except Exception:
            data = _finish(engine, redemption_id, 'unknown', code='redeem_result_unknown',
                           collateral_before=before, actor_id=actor_id, correlation_id=correlation_id)
            return data, 202
        after = _wallet_balance(gateway)

    payout = None
    if before is not None and after is not None:
        with localcontext() as context:
            context.prec = 80
            difference = after - before
        payout = difference if difference > 0 else None
    tx_hash, transaction_id = _transaction_evidence(tx_hash, transaction_id)
    data = _finish(engine, redemption_id, 'confirmed', tx_hash=tx_hash,
                   transaction_id=transaction_id, payout=payout,
                   collateral_before=before, collateral_balance=after,
                   actor_id=actor_id, correlation_id=correlation_id)
    return data, 201


def redeem_single(engine, settings, intent_id, request_id, actor_id, correlation_id=None, gateway_cls=None):
    target = _target_for_single(engine, settings, intent_id)
    return _execute(engine, settings, target, request_id, actor_id, correlation_id, gateway_cls or RedeemGateway)


def redeem_combo(engine, settings, combo_id, request_id, actor_id, correlation_id=None, gateway_cls=None):
    target = _target_for_combo(engine, settings, combo_id)
    return _execute(engine, settings, target, request_id, actor_id, correlation_id, gateway_cls or RedeemGateway)
