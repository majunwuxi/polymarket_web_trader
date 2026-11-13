from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from .config import get_settings
from .database import make_engine
from . import auth, sports_api

logger = logging.getLogger("polysports")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        app.state.settings = settings
        app.state.engine = make_engine(settings)
        logger.info(json.dumps({"event": "application_started", "environment": settings.environment}))
        try:
            yield
        finally:
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

    app.include_router(auth.router)
    app.include_router(sports_api.router)

    @app.get("/api/v1/health/live")
    def health(request: Request):
        return {"data": {"status": "ok"}, "request_id": request.state.request_id}

    return app


app = create_app()
