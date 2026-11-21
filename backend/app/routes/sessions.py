from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

router = APIRouter()


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, request: Request) -> dict[str, Any]:
    db = request.app.state.db
    if await run_in_threadpool(db.get_session, session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    stored = await run_in_threadpool(db.get_messages, session_id)
    messages: list[dict[str, Any]] = []
    for m in stored:
        item: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant":
            item["metadata"] = m.metadata or {}
        messages.append(item)
    return {"session_id": session_id, "messages": messages}
