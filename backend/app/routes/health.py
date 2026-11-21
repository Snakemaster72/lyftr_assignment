from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import __version__

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    state = request.app.state
    db_ok = await run_in_threadpool(state.db.ping)
    keys = state.llm.keys_configured
    healthy = db_ok and keys >= 1
    body = {
        "status": "ok" if healthy else "unavailable",
        "keys_configured": keys,
        "cache_enabled": state.llm.cache_enabled,
        "database": "ok" if db_ok else "unreachable",
        "model": state.settings.models[0],
        "version": __version__,
    }
    if not healthy:
        body["detail"] = "database unreachable" if not db_ok else "no API keys loaded"
    return JSONResponse(body, status_code=200 if healthy else 503)
