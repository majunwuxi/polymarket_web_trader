"""外部赛果候选的查询和管理员确认。"""
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth
from .models import ResultBindingRequest
from .scheduler import db_now, watch_game

router=APIRouter(prefix='/api/v1')


def binding_data(item):
    return {'state':item.state,'provider':item.provider,'fixture_id':item.fixture_id,
            'candidates':item.candidates,'error_code':item.last_error_code}


@router.get('/games/{game_id}/result-binding')
def get_binding(request:Request,game_id:uuid.UUID):
    with Session(request.app.state.engine) as db:
        item=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game_id))
        return {'data':binding_data(item) if item else None,'request_id':request.state.request_id}


class ConfirmBinding(BaseModel):
    fixture_id:str=Field(min_length=1,max_length=255,pattern=r'^[0-9]+$')


@router.post('/games/{game_id}/result-binding/confirm')
def confirm_binding(request:Request,game_id:uuid.UUID,body:ConfirmBinding):
    try:
        with Session(request.app.state.engine) as db,db.begin():
            item=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game_id).with_for_update())
            if not item: return auth.error(request,404,'NOT_FOUND','赛事绑定请求不存在')
            if item.state=='bound':
                if item.fixture_id!=body.fixture_id: return auth.error(request,409,'BINDING_CONFLICT','赛事已绑定其他结果来源')
            elif item.state!='needs_review' or not any(x.get('id')==body.fixture_id for x in item.candidates):
                return auth.error(request,409,'BINDING_INVALID','候选已变化或不存在，请重新核对')
            else:
                item.fixture_id=body.fixture_id;item.state='bound';item.updated_at=db_now(db)
                watch_game(db,game_id);db.flush()
            return {'data':binding_data(item),'request_id':request.state.request_id}
    except IntegrityError:
        return auth.error(request,409,'BINDING_CONFLICT','此外部赛事已绑定其他比赛')
