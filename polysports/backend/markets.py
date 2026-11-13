from __future__ import annotations
import uuid
from datetime import timedelta
import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .gamma import discover, names_match
from .models import Game, MarketLink, MarketOutcome, MarketRefresh, PolyEvent, PolyMarket, Team, TeamAlias
from .scheduler import db_now
from .sports_sources import SourceError, timestamp

FRESH_FOR=timedelta(minutes=15)


def aliases(db: Session, team_id: uuid.UUID) -> list[str]:
    team=db.get(Team,team_id)
    if not team: return []
    result=[team.source_name]
    if team.name_zh and team.translation_status=='verified': result.append(team.name_zh)
    result.extend(db.scalars(select(TeamAlias.alias).where(TeamAlias.team_id==team_id,TeamAlias.verified.is_(True))))
    return result


def ambiguous_name(db: Session, sport: str, name: str) -> bool:
    cache=db.info.setdefault('market_team_names',{})
    if sport not in cache:
        names={team.id:[team.source_name]+([team.name_zh] if team.name_zh and team.translation_status=='verified' else []) for team in db.scalars(select(Team).where(Team.sport==sport))}
        for team_id,alias in db.execute(select(TeamAlias.team_id,TeamAlias.alias).where(TeamAlias.verified.is_(True))):
            if team_id in names: names[team_id].append(alias)
        cache[sport]=names
    return sum(1 for values in cache[sport].values() if names_match(values,name))>1


def evidence_for(game, event, market, ordered, home_ambiguous=False, away_ambiguous=False):
    return {'game_start':game.starts_at.isoformat(),'home_team_id':str(game.home_team_id),'away_team_id':str(game.away_team_id),'event_start':event.starts_at.isoformat(),'event_home':event.home,'event_away':event.away,'ordered':ordered,'market_fingerprint':market.fingerprint,'sport':game.sport,'home_name_ambiguous':home_ambiguous,'away_name_ambiguous':away_ambiguous}


def valid_link(db: Session, link: MarketLink, *, require_confirmed=True) -> bool:
    game=db.get(Game,link.game_id); market=db.get(PolyMarket,link.market_id)
    event=db.get(PolyEvent,market.poly_event_id) if market else None
    refresh=db.get(MarketRefresh,link.game_id)
    if not game or not market or not event or not refresh or refresh.status!='succeeded' or not refresh.finished_at: return False
    now=db_now(db); ev=link.evidence
    if now-refresh.finished_at>FRESH_FOR or now-market.last_verified_at>FRESH_FOR: return False
    if require_confirmed and link.state!='confirmed': return False
    if not require_confirmed and link.state not in ('candidate','stale','confirmed'): return False
    if game.sport!=event.sport or ev.get('sport')!=game.sport or not ev.get('ordered'): return False
    try:
        if timestamp(ev.get('game_start'))!=game.starts_at or timestamp(ev.get('event_start'))!=event.starts_at: return False
    except SourceError:
        return False
    if ev.get('home_team_id')!=str(game.home_team_id) or ev.get('away_team_id')!=str(game.away_team_id): return False
    if ev.get('event_home')!=event.home_name or ev.get('event_away')!=event.away_name or ev.get('market_fingerprint')!=market.fingerprint: return False
    if abs((event.starts_at-game.starts_at).total_seconds())>7200: return False
    if ev.get('home_name_ambiguous')!=ambiguous_name(db,game.sport,event.home_name) or ev.get('away_name_ambiguous')!=ambiguous_name(db,game.sport,event.away_name): return False
    return names_match(aliases(db,game.home_team_id),event.home_name) and names_match(aliases(db,game.away_team_id),event.away_name)


def trading_blockers(db: Session, link: MarketLink) -> list[str]:
    blockers=[]
    if not valid_link(db,link): blockers.append('mapping_unconfirmed_or_stale')
    market=db.get(PolyMarket,link.market_id)
    if not market or market.accepting_orders is not True: blockers.append('accepting_orders_not_confirmed')
    if not market or market.protocol_version not in ('v1','v2'): blockers.append('protocol_unknown')
    if not market or not db.scalar(select(MarketOutcome.id).where(MarketOutcome.market_id==market.id).limit(1)): blockers.append('outcomes_unknown')
    blockers.append('trading_not_implemented')
    return blockers


def begin_refresh(engine,game_id):
    with Session(engine) as db, db.begin():
        game=db.scalar(select(Game).where(Game.id==game_id).with_for_update())
        if not game: return 'not_found'
        now=db_now(db); refresh=db.get(MarketRefresh,game_id)
        if refresh and refresh.status=='running' and now-refresh.started_at<timedelta(minutes=15): return 'running'
        if not refresh: refresh=MarketRefresh(game_id=game_id,status='running',started_at=now); db.add(refresh)
        else: refresh.status='running'; refresh.started_at=now; refresh.finished_at=None; refresh.error_code=None
        return 'started'


def fail_refresh(engine,game_id,started_at,code):
    with Session(engine) as db, db.begin():
        refresh=db.scalar(select(MarketRefresh).where(MarketRefresh.game_id==game_id).with_for_update())
        if not refresh or refresh.started_at!=started_at or refresh.status!='running': return
        now=db_now(db); refresh.status='failed'; refresh.finished_at=now; refresh.error_code=code
        for link in db.scalars(select(MarketLink).where(MarketLink.game_id==game_id,MarketLink.state=='confirmed').with_for_update()): link.state='stale'; link.updated_at=now


def refresh_game(engine,game_id,client: httpx.Client | None=None):
    with Session(engine) as db:
        game=db.get(Game,game_id); refresh=db.get(MarketRefresh,game_id)
        if not game or not refresh or refresh.status!='running': return
        started=refresh.started_at
        snapshot=(game.sport,game.starts_at,game.home_team_id,game.away_team_id)
        home_names,away_names=aliases(db,game.home_team_id),aliases(db,game.away_team_id)
    owned=client is None; source=client or httpx.Client()
    try:
        matches=discover(source,snapshot[0],snapshot[1],home_names,away_names)
        with Session(engine) as db, db.begin():
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('polysports-gamma'))"))
            game=db.scalar(select(Game).where(Game.id==game_id).with_for_update())
            refresh=db.scalar(select(MarketRefresh).where(MarketRefresh.game_id==game_id).with_for_update())
            if not refresh or refresh.status!='running' or refresh.started_at!=started: return
            if not game or (game.sport,game.starts_at,game.home_team_id,game.away_team_id)!=snapshot or aliases(db,game.home_team_id)!=home_names or aliases(db,game.away_team_id)!=away_names:
                raise SourceError('game_changed')
            now=db_now(db); seen=set()
            for source_event,ordered in matches:
                event=db.scalar(select(PolyEvent).where(PolyEvent.gamma_event_id==source_event.gamma_id).with_for_update())
                if not event:
                    event=PolyEvent(id=uuid.uuid4(),gamma_event_id=source_event.gamma_id,sport=source_event.sport,starts_at=source_event.starts_at,home_name=source_event.home,away_name=source_event.away,last_seen_at=now)
                    db.add(event); db.flush()
                event.title,event.slug,event.sport,event.starts_at,event.home_name,event.away_name,event.last_seen_at,event.updated_at=source_event.title,source_event.slug,source_event.sport,source_event.starts_at,source_event.home,source_event.away,now,now
                for sm in source_event.markets:
                    market=db.scalar(select(PolyMarket).where(PolyMarket.gamma_market_id==sm.gamma_id).with_for_update())
                    if not market:
                        market=PolyMarket(id=uuid.uuid4(),gamma_market_id=sm.gamma_id,poly_event_id=event.id,condition_id=sm.condition_id,fingerprint=sm.fingerprint,last_verified_at=now)
                        db.add(market);db.flush()
                    else:
                        if market.poly_event_id!=event.id or market.condition_id!=sm.condition_id: raise SourceError('market_identity_changed')
                        old=list(db.scalars(select(MarketOutcome).where(MarketOutcome.market_id==market.id).order_by(MarketOutcome.outcome_index)))
                        if old and [(x.outcome_index,x.label,x.asset_id,x.asset_kind) for x in old]!=[(x.index,x.label,x.asset_id,x.kind) for x in sm.outcomes]: raise SourceError('market_outcomes_changed')
                        if market.fingerprint!=sm.fingerprint:
                            for old_link in db.scalars(select(MarketLink).where(MarketLink.market_id==market.id,MarketLink.state=='confirmed').with_for_update()): old_link.state='stale';old_link.updated_at=now
                    market.protocol_version,market.slug,market.question,market.accepting_orders,market.combo_enabled,market.fingerprint,market.last_verified_at,market.updated_at=sm.version,sm.slug,sm.question,sm.accepting_orders,None,sm.fingerprint,now,now
                    for outcome in sm.outcomes:
                        existing=db.scalar(select(MarketOutcome).where(MarketOutcome.market_id==market.id,MarketOutcome.outcome_index==outcome.index))
                        if not existing: db.add(MarketOutcome(id=uuid.uuid4(),market_id=market.id,outcome_index=outcome.index,label=outcome.label,asset_id=outcome.asset_id,asset_kind=outcome.kind))
                    db.flush(); seen.add(market.id)
                    ev=evidence_for(game,source_event,sm,ordered,ambiguous_name(db,game.sport,source_event.home),ambiguous_name(db,game.sport,source_event.away))
                    link=db.scalar(select(MarketLink).where(MarketLink.game_id==game.id,MarketLink.market_id==market.id).with_for_update())
                    if not link: db.add(MarketLink(id=uuid.uuid4(),game_id=game.id,market_id=market.id,state='candidate',match_method='manual',evidence=ev))
                    elif link.state!='rejected':
                        if link.evidence!=ev or link.state=='stale': link.state='candidate'; link.confirmed_at=None; link.confirmed_by=None
                        link.evidence=ev; link.updated_at=now
            for link in db.scalars(select(MarketLink).where(MarketLink.game_id==game.id,MarketLink.state.in_(['candidate','confirmed'])).with_for_update()):
                if link.market_id not in seen: link.state='stale'; link.updated_at=now
            refresh.status='succeeded';refresh.finished_at=now;refresh.error_code=None
    except SourceError as exc:
        fail_refresh(engine,game_id,started,exc.code)
    except Exception:
        fail_refresh(engine,game_id,started,'internal_error')
    finally:
        if owned: source.close()
