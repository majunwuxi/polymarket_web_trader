from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .config import get_settings
from .database import make_engine

logger = logging.getLogger("polysports")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/api/v1/health/live")
    def health(request: Request):
        return {"data": {"status": "ok"}, "request_id": request.state.request_id}

    return app


app = create_app()
