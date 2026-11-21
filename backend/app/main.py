"""FastAPI app factory. Run with: uvicorn --factory app.main:create_app"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, load_settings
from app.db import Database
from app.llm import LLMGateway
from app.log_config import configure_logging
from app.routes import chat, health, sessions

log = logging.getLogger("app.request")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_path)
        db.init_schema()
        llm = LLMGateway(settings)
        await llm.warm_up()
        app.state.settings, app.state.db, app.state.llm = settings, db, llm
        logging.getLogger("app").info(
            "started",
            extra={
                "fields": {
                    "keys": llm.keys_configured,
                    "models": settings.models,
                    "cache": settings.cache_type,
                    "failure_simulation": settings.enable_failure_simulation,
                }
            },
        )
        yield

    docs = settings.enable_docs
    app = FastAPI(
        title="Lyftr chatbot API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Last line of defence: log the traceback, never send it.
            log.exception("unhandled error", extra={"fields": {"request_id": request_id}})
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        fields = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            **getattr(request.state, "log_fields", {}),
        }
        log.info("request", extra={"fields": fields})
        return response

    # Added after the middleware above, so it wraps it: CORS headers also go on error responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Simulate-Failure", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    return app
