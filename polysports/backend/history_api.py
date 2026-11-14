"""历史、统计与导出：仅使用已持久化证据，测试上游必须为模拟数据。"""
from __future__ import annotations

import csv
import io
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import exists, func, literal, select, tuple_, union_all
from sqlalchemy.orm import Session

from . import auth
from .combo_api import combo_data
from .models import AuditEvent, ComboLeg, ComboRFQ, Game, Job, SyncRun, Team, TradeFill, TradeIntent, WorkerHeartbeat
from .scheduler import db_now
from .sports_api import cursor_decode, cursor_encode, game_data
from .trading import as_data

router=APIRouter(prefix='/api/v1')
TZ8=timezone(timedelta(hours=8))
EXCEL_ROWS=1_048_576


class HistoryPage(BaseModel):
    items:list[dict]
    next_cursor:str|None


class HistoryPageResponse(BaseModel):
    data:HistoryPage
    request_id:str


class StatisticsData(BaseModel):
    games_total:int
    finished_total:int
    trade_count:int
    confirmed_fill_count:int
    confirmed_combo_count:int
    realized_pnl:str|None
    currency:str
    as_of:str
    scope:str


class StatisticsResponse(BaseModel):
    data:StatisticsData
    request_id:str


def envelope(request,data):return {'data':data,'request_id':request.state.request_id}


def valid_range(start,end):
    return (start is None or start.tzinfo is not None) and (end is None or end.tzinfo is not None) and (start is None or end is None or end>start)


def game_query(sport,start,end,status=None):
    query=select(Game)
    if sport:query=query.where(Game.sport==sport)
    if start:query=query.where(Game.starts_at>=start)
    if end:query=query.where(Game.starts_at<end)
    if status:query=query.where(Game.status==status)
    return query


def trade_query(sport,start,end,wallet_id=None,kind=None,state=None):
    single=select(TradeIntent.id.label('id'),TradeIntent.created_at.label('created_at'),literal('single').label('kind')).join(Game,Game.id==TradeIntent.game_id)
    combo=select(ComboRFQ.id.label('id'),ComboRFQ.created_at.label('created_at'),literal('combo').label('kind'))
    if sport:
        single=single.where(Game.sport==sport)
        combo=combo.where(exists(select(ComboLeg.id).join(Game,Game.id==ComboLeg.game_id).where(ComboLeg.rfq_id==ComboRFQ.id,Game.sport==sport)))
    for model in (TradeIntent,ComboRFQ):
        target=single if model is TradeIntent else combo
        if start:target=target.where(model.created_at>=start)
        if end:target=target.where(model.created_at<end)
        if wallet_id:target=target.where(model.wallet_id==wallet_id)
        if state:target=target.where(model.state==state)
        if model is TradeIntent:single=target
        else:combo=target
    if kind=='single':return single.subquery()
    if kind=='combo':return combo.subquery()
    return union_all(single,combo).subquery()


def trade_data(db,kind,ident):
    if kind=='single':
        row=db.get(TradeIntent,ident)
        data=as_data(row,db);data['kind']='single'
        data['confirmed_fills']=db.scalar(select(func.count(TradeFill.id)).where(TradeFill.intent_id==ident,func.upper(TradeFill.status)=='CONFIRMED'))
        return data
    data=combo_data(db,db.get(ComboRFQ,ident));data['kind']='combo';data['confirmed_fills']=None
    return data


def page_cursor(cursor):
    date,ident=cursor_decode(cursor);date=datetime.fromisoformat(date)
    if date.tzinfo is None:raise ValueError()
    return date,uuid.UUID(ident)


@router.get('/history/games',response_model=HistoryPageResponse)
def history_games(request:Request,sport:Literal['football','nba']|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),status:str|None=None,limit:int=Query(50,ge=1,le=100),cursor:str|None=None):
    if not valid_range(start,end):return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    query=game_query(sport,start,end,status)
    try:
        if cursor:
            stamp,ident=page_cursor(cursor);query=query.where(tuple_(Game.starts_at,Game.id)<tuple_(stamp,ident))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(Game.starts_at.desc(),Game.id.desc()).limit(limit+1)));items=rows[:limit]
        return envelope(request,{'items':[game_data(db,x) for x in items],'next_cursor':cursor_encode([items[-1].starts_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None})


@router.get('/history/trades',response_model=HistoryPageResponse)
def history_trades(request:Request,sport:Literal['football','nba']|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),wallet_id:uuid.UUID|None=None,kind:Literal['single','combo']|None=None,state:str|None=None,limit:int=Query(50,ge=1,le=100),cursor:str|None=None):
    if not valid_range(start,end):return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    source=trade_query(sport,start,end,wallet_id,kind,state);query=select(source)
    try:
        if cursor:
            stamp,key=cursor_decode(cursor);stamp=datetime.fromisoformat(stamp);ident,kind_key=key.rsplit(':',1)
            if stamp.tzinfo is None or kind_key not in ('single','combo'):raise ValueError()
            query=query.where(tuple_(source.c.created_at,source.c.id,source.c.kind)<tuple_(stamp,uuid.UUID(ident),kind_key))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.execute(query.order_by(source.c.created_at.desc(),source.c.id.desc(),source.c.kind.desc()).limit(limit+1)));items=rows[:limit]
        return envelope(request,{'items':[trade_data(db,x.kind,x.id) for x in items],'next_cursor':cursor_encode([items[-1].created_at.isoformat(),str(items[-1].id)+':'+items[-1].kind]) if len(rows)>limit else None})


@router.get('/statistics',response_model=StatisticsResponse)
def statistics(request:Request,sport:Literal['football','nba']|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),wallet_id:uuid.UUID|None=None,kind:Literal['single','combo']|None=None,state:str|None=None,status:str|None=None):
    if not valid_range(start,end):return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    games=game_query(sport,start,end,status).subquery();trades=trade_query(sport,start,end,wallet_id,kind,state)
    with Session(request.app.state.engine) as db:
        game_count=db.scalar(select(func.count()).select_from(games))
        finished=db.scalar(select(func.count()).select_from(games).where(games.c.status=='finished'))
        trade_count=db.scalar(select(func.count()).select_from(trades))
        fill_count=db.scalar(select(func.count(TradeFill.id)).where(func.upper(TradeFill.status)=='CONFIRMED',TradeFill.intent_id.in_(select(trades.c.id).where(trades.c.kind=='single'))))
        combo_confirmed=db.scalar(select(func.count()).select_from(trades).join(ComboRFQ,ComboRFQ.id==trades.c.id).where(trades.c.kind=='combo',ComboRFQ.state=='confirmed'))
        return envelope(request,{'games_total':game_count,'finished_total':finished,'trade_count':trade_count,'confirmed_fill_count':fill_count,'confirmed_combo_count':combo_confirmed,'realized_pnl':None,'currency':'pUSD','as_of':db_now(db).isoformat(),'scope':'成交仅计已保存的 CONFIRMED 证据；组合已确认单独计数。收益证据不足。'})


def spreadsheet_text(value):
    text='' if value is None else str(value)
    # 控制字符和前置空白不可掩盖公式前缀。
    if text.lstrip(' \t\r\n').startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')):return "'"+text
    return text


def display_time(value):
    if value is None:return ''
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    return value.astimezone(TZ8).strftime('%Y-%m-%d %H:%M:%S UTC+8')


GAME_HEADERS=['赛事ID','来源','来源ID','运动','开赛时间（UTC+8）','主队','客队','主队比分','客队比分','状态']
TRADE_HEADERS=['记录ID','类型','钱包ID','创建时间（UTC+8）','方向','价格','数量','已成交份额','状态','外部ID','赛事与选项','已实现收益']


def export_game_row(data):
    return [data['id'],data['provider'],data['external_id'],data['sport'],display_time(data['starts_at']),data['home_team']['name_zh'] or data['home_team']['source_name'],data['away_team']['name_zh'] or data['away_team']['source_name'],data['home_score'],data['away_score'],data['status']]


def export_trade_row(data):
    if data['kind']=='single':
        event=f"{data['home']} - {data['away']} | {data['market_question'] or ''} | {data['outcome_label']}"
        return [data['id'],'single',data['wallet_id'],display_time(data['created_at']),data['side'],data['limit_price'],data['size'],data['matched_size'],data['state'],data['external_order_id'],event,None]
    event='; '.join(f"{x['home']} - {x['away']} | {x['market_question'] or ''} | {x['outcome_label']}" for x in data['legs'])
    return [data['id'],'combo',data['wallet_id'],display_time(data['created_at']),data['direction'],data['quote']['price'] if data['quote'] else None,data['quantity'],None,data['state'],data['external_rfq_id'],event,None]


def safe_row(values):return [spreadsheet_text(x) if not isinstance(x,(int,float,Decimal)) else x for x in values]


def output_rows(engine,kind,sport,start,end,status,wallet_id,trade_kind):
    with Session(engine) as db:
        if kind=='games':
            rows=db.scalars(game_query(sport,start,end,status).order_by(Game.starts_at.desc(),Game.id.desc()).execution_options(yield_per=200))
            for row in rows:yield safe_row(export_game_row(game_data(db,row)))
        else:
            source=trade_query(sport,start,end,wallet_id,trade_kind,status)
            rows=db.execute(select(source).order_by(source.c.created_at.desc(),source.c.id.desc(),source.c.kind.desc()).execution_options(yield_per=200))
            for row in rows:yield safe_row(export_trade_row(trade_data(db,row.kind,row.id)))


def csv_bytes(headers,rows):
    buffer=io.StringIO(newline='');writer=csv.writer(buffer,lineterminator='\r\n')
    yield b'\xef\xbb\xbf'
    for row in [headers]:
        writer.writerow(row);yield buffer.getvalue().encode('utf-8');buffer.seek(0);buffer.truncate(0)
    for row in rows:
        writer.writerow(row);yield buffer.getvalue().encode('utf-8');buffer.seek(0);buffer.truncate(0)


def excel_bytes(headers,rows):
    spool=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
    try:
        workbook=Workbook(write_only=True);sheet=None;count=EXCEL_ROWS
        for row in rows:
            if count>=EXCEL_ROWS:
                sheet=workbook.create_sheet(f'记录{len(workbook.sheetnames)+1}');sheet.append(headers);count=1
            sheet.append(row);count+=1
        if sheet is None:sheet=workbook.create_sheet('记录1');sheet.append(headers)
        workbook.save(spool);spool.seek(0)
        while chunk:=spool.read(65536):yield chunk
    finally:spool.close()


def export(request:Request,resource:Literal['games','trades'],extension:Literal['csv','xlsx'],sport:Literal['football','nba']|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),status:str|None=None,state:str|None=None,wallet_id:uuid.UUID|None=None,kind:Literal['single','combo']|None=None):
    if not valid_range(start,end):return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    headers=GAME_HEADERS if resource=='games' else TRADE_HEADERS
    rows=output_rows(request.app.state.engine,resource,sport,start,end,status if resource=='games' else state,wallet_id,kind)
    body=csv_bytes(headers,rows) if extension=='csv' else excel_bytes(headers,rows)
    media='text/csv; charset=utf-8' if extension=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return StreamingResponse(body,media_type=media,headers={'Content-Disposition':f'attachment; filename="polysports-{resource}.{extension}"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def export_route(resource,extension):
    def handler(request:Request,sport:Literal['football','nba']|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),status:str|None=None,state:str|None=None,wallet_id:uuid.UUID|None=None,kind:Literal['single','combo']|None=None):
        return export(request,resource,extension,sport,start,end,status,state,wallet_id,kind)
    handler.__name__='export_'+resource+'_'+extension
    return handler


for _resource in ('games','trades'):
    for _extension in ('csv','xlsx'):
        _media='text/csv' if _extension=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        router.add_api_route('/exports/'+_resource+'.'+_extension,export_route(_resource,_extension),methods=['GET'],response_class=StreamingResponse,responses={200:{'description':'导出文件','content':{_media:{'schema':{'type':'string','format':'binary'}}}}})


@router.get('/system/status')
def system_status(request:Request):
    with Session(request.app.state.engine) as db:
        now=db_now(db);workers=list(db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(20)))
        sources=[]
        for sport in ('football','nba'):
            run=db.scalar(select(SyncRun).where(SyncRun.sport==sport,SyncRun.started_at.is_not(None)).order_by(SyncRun.started_at.desc(),SyncRun.id.desc()).limit(1))
            next_due=db.scalar(select(func.min(Job.due_at)).join(SyncRun,SyncRun.id==Job.sync_run_id).where(SyncRun.sport==sport,Job.status=='pending',Job.kind=='sync'))
            sources.append({'sport':sport,'status':run.status if run else 'unknown','last_checked_at':run.finished_at.isoformat() if run and run.finished_at else None,'error_code':run.error_code if run else None,'next_due_at':next_due.isoformat() if next_due else None})
        settings=request.app.state.settings
        return envelope(request,{'database':{'status':'ok','checked_at':now.isoformat()},'workers':[{'id':str(x.worker_id),'status':x.state if (now-x.last_seen_at).total_seconds()<60 else 'unavailable','last_seen_at':x.last_seen_at.isoformat()} for x in workers],'sources':sources,'configuration':{'football_key_configured':bool(settings.api_football_key and settings.api_football_key.get_secret_value()),'nba_key_configured':bool(settings.balldontlie_key and settings.balldontlie_key.get_secret_value()),'wallet_count':len(settings.wallets),'combo_enabled':settings.combo_execution_enabled}})


@router.get('/audit-events',response_model=HistoryPageResponse)
def audit_events(request:Request,action:str|None=None,entity_type:str|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),limit:int=Query(50,ge=1,le=100),cursor:str|None=None):
    if not valid_range(start,end):return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    query=select(AuditEvent)
    if action:query=query.where(AuditEvent.action==action)
    if entity_type:query=query.where(AuditEvent.entity_type==entity_type)
    if start:query=query.where(AuditEvent.created_at>=start)
    if end:query=query.where(AuditEvent.created_at<end)
    try:
        if cursor:
            stamp,ident=page_cursor(cursor);query=query.where(tuple_(AuditEvent.created_at,AuditEvent.id)<tuple_(stamp,ident))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(AuditEvent.created_at.desc(),AuditEvent.id.desc()).limit(limit+1)));items=rows[:limit]
        # 详情仅返回预先审查过的原因代码，不回显任意 JSON。
        return envelope(request,{'items':[{'id':str(x.id),'action':x.action,'entity_type':x.entity_type,'entity_id':str(x.entity_id) if x.entity_id else None,'created_at':x.created_at.isoformat()} for x in items],'next_cursor':cursor_encode([items[-1].created_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None})
