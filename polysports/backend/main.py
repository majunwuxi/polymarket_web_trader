from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text

from .config import get_settings
from .database import make_engine
from . import auth, sports_api, scheduler_api, market_api, trading_api, combo_api, history_api

logger = logging.getLogger("polysports")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        app.state.settings = settings
        app.state.engine = make_engine(settings)
        combo_lock = None
        if settings.combo_execution_enabled:
            try:
                combo_lock = app.state.engine.connect()
                if not combo_lock.scalar(text("SELECT pg_try_advisory_lock(hashtext('polysports-combo-api'))")):
                    raise RuntimeError('组合交易仅支持单个 API 进程')
                combo_lock.commit()
            except BaseException:
                if combo_lock is not None: combo_lock.close()
                app.state.engine.dispose()
                raise
        logger.info(json.dumps({"event": "application_started", "environment": settings.environment}))
        try:
            yield
        finally:
            if combo_lock is not None: combo_lock.close()
            app.state.engine.dispose()
            logger.info(json.dumps({"event": "application_stopped"}))

    app = FastAPI(title="PolySports", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        try:
            request_id = str(uuid.UUID(request.headers.get("X-Request-ID", "")))
        except (ValueError, TypeError):
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        path = request.url.path
        public = {"/api/v1/health/live", "/api/v1/auth/csrf", "/api/v1/auth/login"}
        try:
            if path.startswith("/api/v1/"):
                session = None if path in public else auth.get_session(request)
                request.state.admin_session = session
                if path not in public and session is None:
                    response = auth.error(request, 401, "UNAUTHENTICATED", "请先登录")
                elif request.method in ("POST", "PUT", "PATCH", "DELETE") and not auth.check_csrf(request, session):
                    response = auth.error(request, 403, "CSRF_INVALID", "请求来源或 CSRF 校验失败")
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception as exc:
            logger.error("request_failed type=%s request_id=%s", type(exc).__name__, request_id)
            response = auth.error(request, 503, "SERVICE_UNAVAILABLE", "服务暂不可用")
        if path.startswith("/api/v1/auth/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return auth.error(request, 422, "VALIDATION_ERROR", "请求参数不符合要求")

    def openapi_schema():
        if app.openapi_schema is not None:return app.openapi_schema
        schema=get_openapi(title=app.title,version=app.version,routes=app.routes)
        schema.setdefault('components',{}).setdefault('securitySchemes',{})['AdminSession']={'type':'apiKey','in':'cookie','name':auth.SESSION_COOKIE,'description':'管理员会话；所有修改请求还须携带同源 Origin 与会话绑定 CSRF 令牌。'}
        public={'/api/v1/health/live','/api/v1/auth/csrf','/api/v1/auth/login'}
        for path,methods in schema.get('paths',{}).items():
            if not path.startswith('/api/v1/'):continue
            for method,operation in methods.items():
                if method not in ('get','post','put','patch','delete'):continue
                if path not in public:
                    operation['security']=[{'AdminSession':[]}]
                    operation.setdefault('responses',{}).setdefault('401',{'description':'未认证'})
                if method in ('post','put','patch','delete'):
                    operation.setdefault('parameters',[]).extend([{'name':'Origin','in':'header','required':True,'schema':{'type':'string'}},{'name':'X-CSRF-Token','in':'header','required':True,'schema':{'type':'string'}}])
                    operation.setdefault('responses',{}).setdefault('403',{'description':'同源或 CSRF 校验失败'})
        app.openapi_schema=schema
        return schema
    app.openapi=openapi_schema

    app.include_router(auth.router)
    app.include_router(sports_api.router)
    app.include_router(scheduler_api.router)
    app.include_router(market_api.router)
    app.include_router(trading_api.router)
    app.include_router(combo_api.router)
    app.include_router(history_api.router)

    @app.get("/api/v1/health/live")
    def health(request: Request):
        return {"data": {"status": "ok"}, "request_id": request.state.request_id}

    return app


app = create_app()
