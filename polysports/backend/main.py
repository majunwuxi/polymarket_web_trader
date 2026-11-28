from __future__ import annotations

import json
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text

from . import (
    auth,
    combo_api,
    history_api,
    market_api,
    redeem_api,
    result_binding_api,
    scheduler_api,
    sports_api,
    trading_api,
)
from .config import get_settings
from .database import make_engine

logger = logging.getLogger("polysports")

_ID = r'[0-9a-fA-F-]{36}'
_AGENT_READ_PATH = re.compile(
    rf'/api/v1/(?:games(?:/{_ID}/markets)?|wallets(?:/{_ID}/(?:status|trade-limits))?|combo-legs/{_ID}/{_ID})'
)
_DOCS_PATHS = {'/docs', '/docs/', '/docs/oauth2-redirect', '/redoc', '/openapi.json'}
_AGENT_FAILURE_LIMIT = 20
_AGENT_FAILURE_WINDOW = 60
_agent_token_failures: dict[str, list[float]] = {}


def agent_read_allowed(request: Request) -> bool:
    if request.method != 'GET' or not _AGENT_READ_PATH.fullmatch(request.url.path):
        return False
    configured = request.app.state.settings.agent_read_token
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    if not configured or scheme.lower() != 'bearer':
        return False
    expected = configured.get_secret_value()
    return len(expected) >= 32 and secrets.compare_digest(token.encode(), expected.encode())


def agent_token_failure(request: Request) -> bool:
    """记录脱敏的只读令牌失败并按来源 IP 限流；返回是否已超过阈值。"""
    now = time.monotonic()
    source = request.client.host if request.client else 'unknown'
    history = [item for item in _agent_token_failures.get(source, []) if item > now - _AGENT_FAILURE_WINDOW]
    history.append(now)
    _agent_token_failures[source] = history
    logger.warning(json.dumps({
        'event': 'agent_token_rejected',
        'source': source,
        'method': request.method,
        'path': request.url.path,
        'attempts': len(history),
    }))
    if len(_agent_token_failures) > 1024:
        cutoff = now - _AGENT_FAILURE_WINDOW
        for stale in [key for key, value in _agent_token_failures.items() if not value or value[-1] <= cutoff]:
            _agent_token_failures.pop(stale, None)
    return len(history) > _AGENT_FAILURE_LIMIT


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
                if combo_lock is not None:
                    combo_lock.close()
                app.state.engine.dispose()
                raise
        logger.info(json.dumps({"event": "application_started", "environment": settings.environment}))
        try:
            with httpx.Client() as market_client:
                app.state.market_client = market_client
                yield
        finally:
            if combo_lock is not None:
                combo_lock.close()
            from .trading_gateway import close_gateway_clients

            close_gateway_clients()
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
            settings = getattr(request.app.state, 'settings', None)
            if path in _DOCS_PATHS and settings is not None and settings.environment == 'production':
                # 生产环境不暴露交互式文档与 OpenAPI 描述。
                response = Response(status_code=404)
            elif path.startswith("/api/v1/"):
                agent_read = agent_read_allowed(request)
                unauthorized_agent = (
                    not agent_read
                    and request.method == 'GET'
                    and bool(request.headers.get('Authorization'))
                    and bool(_AGENT_READ_PATH.fullmatch(path))
                )
                if unauthorized_agent and agent_token_failure(request):
                    response = auth.error(request, 429, "RATE_LIMITED", "只读令牌验证失败过于频繁，请稍后重试", 60)
                else:
                    session = None if path in public or agent_read else await run_in_threadpool(auth.get_session, request)
                    request.state.admin_session = session
                    if path not in public and session is None and not agent_read:
                        response = auth.error(request, 401, "UNAUTHENTICATED", "请先登录")
                    elif request.method in ("POST", "PUT", "PATCH", "DELETE") and not auth.check_csrf(request, session):
                        response = auth.error(request, 403, "CSRF_INVALID", "请求来源或 CSRF 校验失败")
                    else:
                        response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception:
            # 未捕获异常必须记录完整堆栈，否则下单 503 只剩类型名，无法定位真实原因。
            logger.exception("request_failed request_id=%s method=%s path=%s", request_id, request.method, path)
            response = auth.error(
                request, 503, "SERVICE_UNAVAILABLE", "服务暂不可用，请提供请求编号 " + request_id + " 以便排查"
            )
        if path.startswith("/api/v1/auth/") or request.headers.get('Authorization'):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return auth.error(request, 422, "VALIDATION_ERROR", "请求参数不符合要求")

    def openapi_schema():
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schema.setdefault('components', {}).setdefault('securitySchemes', {})['AdminSession'] = {
            'type': 'apiKey',
            'in': 'cookie',
            'name': auth.SESSION_COOKIE,
            'description': '管理员会话；所有修改请求还须携带同源 Origin 与会话绑定 CSRF 令牌。',
        }
        public = {'/api/v1/health/live', '/api/v1/auth/csrf', '/api/v1/auth/login'}
        for path, methods in schema.get('paths', {}).items():
            if not path.startswith('/api/v1/'):
                continue
            for method, operation in methods.items():
                if method not in ('get', 'post', 'put', 'patch', 'delete'):
                    continue
                if path not in public:
                    operation['security'] = [{'AdminSession': []}]
                    operation.setdefault('responses', {}).setdefault('401', {'description': '未认证'})
                if method in ('post', 'put', 'patch', 'delete'):
                    operation.setdefault('parameters', []).extend(
                        [
                            {'name': 'Origin', 'in': 'header', 'required': True, 'schema': {'type': 'string'}},
                            {'name': 'X-CSRF-Token', 'in': 'header', 'required': True, 'schema': {'type': 'string'}},
                        ]
                    )
                    operation.setdefault('responses', {}).setdefault('403', {'description': '同源或 CSRF 校验失败'})
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_schema

    app.include_router(auth.router)
    app.include_router(sports_api.router)
    app.include_router(result_binding_api.router)
    app.include_router(scheduler_api.router)
    app.include_router(market_api.router)
    app.include_router(trading_api.router)
    app.include_router(combo_api.router)
    app.include_router(history_api.router)
    app.include_router(redeem_api.router)

    @app.get("/api/v1/health/live")
    def health(request: Request):
        return {"data": {"status": "ok"}, "request_id": request.state.request_id}

    return app


app = create_app()
