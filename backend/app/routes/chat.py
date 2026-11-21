import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.db import StoredMessage
from app.llm import AllProvidersUnavailableError, SimulationHeaderError, parse_simulation_header
from app.schemas import ChatMetadata, ChatRequest, ChatResponse

router = APIRouter()


def build_llm_messages(
    system_prompt: str, history: list[StoredMessage], message: str, max_history: int
) -> list[dict[str, str]]:
    """[system] + stored turns (oldest first, optionally only the last N) + the new user message."""
    turns = history[-max_history:] if max_history > 0 else history
    while turns and turns[0].role != "user":  # a capped window should start on a user turn
        turns = turns[1:]
    return [
        {"role": "system", "content": system_prompt},
        *({"role": m.role, "content": m.content} for m in turns),
        {"role": "user", "content": message},
    ]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    x_simulate_failure: Annotated[str | None, Header()] = None,
) -> ChatResponse:
    started = time.perf_counter()
    state = request.app.state
    settings, db, llm = state.settings, state.db, state.llm
    request.state.log_fields = {"session_id": body.session_id}

    simulation = None
    if settings.enable_failure_simulation and x_simulate_failure and x_simulate_failure.strip():
        try:
            simulation = parse_simulation_header(x_simulate_failure, llm.key_ids)
        except SimulationHeaderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        request.state.log_fields["simulation"] = x_simulate_failure

    session = await run_in_threadpool(db.get_session, body.session_id)
    history = await run_in_threadpool(db.get_messages, body.session_id) if session else []
    system_prompt = body.system_prompt or (session and session.system_prompt) or settings.default_system_prompt
    messages = build_llm_messages(system_prompt, history, body.message, settings.history_max_messages)

    try:
        result = await llm.complete(messages, simulation)
    except AllProvidersUnavailableError:
        raise HTTPException(status_code=503, detail="all providers unavailable") from None

    metadata = ChatMetadata(
        resolution_source="LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
        model=result.model,
        latency_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        key_id=result.key_id,
        fallback_attempts=result.fallback_attempts,
    )
    await run_in_threadpool(
        db.save_turn, body.session_id, body.message, result.reply, metadata.model_dump(), body.system_prompt
    )
    request.state.log_fields.update(
        source=metadata.resolution_source,
        key_id=metadata.key_id,
        fallback_attempts=metadata.fallback_attempts,
    )
    return ChatResponse(session_id=body.session_id, reply=result.reply, metadata=metadata)
