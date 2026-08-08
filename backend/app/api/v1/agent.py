"""Agent chat endpoints — synchronous and Server-Sent Events.

The SSE stream carries *real* node events, not a simulated progress animation.
``LangGraph.astream(stream_mode="updates")`` yields one payload per node as it
completes, so the chain-of-thought the UI renders is the graph's actual
execution path — including which agents ran in parallel and which tools each
one called.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.graph import agent_graph, set_session, topology
from app.agent.tools import ToolDenied, resolve_scope
from app.api.deps import build_patient_context
from app.core.db import SessionLocal, get_db
from app.core.security import get_current_user
from app.models.identity import AuditLog, User
from app.privacy.boundary import egress_policy_note
from app.services.ai_orchestrator import orchestrator_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    # Guardians ask about a dependent; patients may only ask about themselves.
    subject_user_id: str | None = None
    language: str | None = None


def _initial_state(db: Session, user: User, payload: AgentRequest) -> dict:
    try:
        scope = resolve_scope(db, user, payload.subject_user_id)
    except ToolDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # The full clinical context is assembled here, but the PHI boundary inside
    # each agent minimises it per route before any of it reaches a prompt.
    context = build_patient_context(db, scope["subject_user_id"])

    return {
        "user_text": payload.message.strip(),
        "language": payload.language or str(user.preferred_language),
        "user_id": user.id,
        "role": str(user.role),
        "session_id": payload.session_id or uuid.uuid4().hex,
        "scope": scope,
        "safe_context": context,
        "agent_outputs": [],
        "trace": [],
    }


def _audit(db: Session, user: User, state: dict, result: dict) -> None:
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_role=str(user.role),
            action="agent.query",
            resource_type="agent_session",
            resource_id=state.get("session_id"),
            detail={
                "routes": [r.get("route") for r in (result.get("routes") or [])],
                "guard": result.get("guard", {}),
                "subject_user_id": state["scope"].get("subject_user_id"),
                "trace_nodes": [t.get("node") for t in (result.get("trace") or [])],
            },
        )
    )
    db.commit()


def _response(state: dict, result: dict) -> dict:
    return {
        "session_id": state["session_id"],
        "answer": result.get("answer", ""),
        "routes": [r.get("route") for r in (result.get("routes") or [])],
        "citations": result.get("citations", []),
        "guard": result.get("guard", {}),
        "trace": result.get("trace", []),
        "structured": {
            output["route"]: output.get("structured", {})
            for output in (result.get("agent_outputs") or [])
        },
    }


@router.post("/chat")
def agent_chat(
    payload: AgentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Synchronous turn. Same contract as the stream's final event."""
    state = _initial_state(db, current_user, payload)
    set_session(db)
    try:
        result = dict(agent_graph.invoke(state))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent graph failed")
        raise HTTPException(
            status_code=500, detail="The assistant could not complete that request."
        ) from exc

    _audit(db, current_user, state, result)
    return _response(state, result)


@router.post("/stream")
async def agent_stream(
    payload: AgentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream real node events as the graph executes."""
    state = _initial_state(db, current_user, payload)

    async def events() -> AsyncIterator[str]:
        # Open the stream immediately so proxies don't buffer the first chunk.
        yield ": open\n\n"
        final: dict[str, Any] = {}

        try:
            # astream() is used rather than stream() on a worker thread.
            # LangGraph runs the sync nodes in its own executor and drives the
            # event loop correctly; bridging a sync generator into asyncio by
            # hand deadlocked once fan-out branches were involved.
            async for update in agent_graph.astream(state, stream_mode="updates"):
                for node, delta in update.items():
                    final.update(
                        {
                            key: value
                            for key, value in (delta or {}).items()
                            if key not in ("trace", "agent_outputs")
                        }
                    )
                    for entry in (delta or {}).get("trace", []) or []:
                        final.setdefault("trace", []).append(entry)
                    for entry in (delta or {}).get("agent_outputs", []) or []:
                        final.setdefault("agent_outputs", []).append(entry)

                    if node == "route":
                        selected = (delta or {}).get("routes", []) or []
                        yield _sse({
                            "type": "routes",
                            "routes": [r["route"] for r in selected],
                            "parallel": len(selected) > 1,
                        })

                    # One event per completed node, carrying its own trace.
                    for entry in (delta or {}).get("trace", []) or []:
                        yield _sse({"type": "stage", "node": node, **entry})
                        for tool in entry.get("tools", []) or []:
                            yield _sse({"type": "tool", "node": node, **tool})

            yield _sse({"type": "final", **_response(state, final)})
        except Exception:  # noqa: BLE001
            logger.exception("Agent stream failed")
            yield _sse({"type": "error", "detail": "The assistant failed."})
        finally:
            # Audit on a private session: the request-scoped one may already be
            # closed by the time the stream finishes.
            audit_db = SessionLocal()
            try:
                _audit(audit_db, current_user, state, final)
            except Exception:  # noqa: BLE001
                logger.warning("Agent audit write failed", exc_info=True)
            finally:
                audit_db.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.get("/status")
def agent_status(current_user: User = Depends(get_current_user)) -> dict:
    """Live agent configuration, including the honest privacy caveat."""
    status = orchestrator_status()
    return {
        "orchestrator": status,
        "graph": topology(),
        "privacy": egress_policy_note(gemini_free_tier=status["gemini_reachable"]),
    }
