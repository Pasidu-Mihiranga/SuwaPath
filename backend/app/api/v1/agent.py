"""Assistant endpoints — chat, streaming, history, and private sessions.

The SSE stream carries *real* node events, not a simulated progress animation.
``LangGraph.astream(stream_mode="updates")`` yields one payload per node as it
completes, so the chain-of-thought the UI renders is the graph's actual
execution path — including which agents ran in parallel, which tools each one
called, and whether the answer came from cache or a model.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import cag, consult
from app.agent.graph import agent_graph, set_session, topology
from app.agent.tools import ToolDenied, resolve_scope
from app.api.deps import build_patient_context
from app.core.db import SessionLocal, get_db
from app.core.security import get_current_user
from app.models.chat import ChatSession
from app.models.identity import AuditLog, User
from app.privacy.boundary import egress_policy_note
from app.services import chat_store, memory
from app.services.ai_orchestrator import orchestrator_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    # Guardians ask about a dependent; patients may only ask about themselves.
    subject_user_id: str | None = None
    language: str | None = None
    # Start a private session on the first turn.
    private: bool = False
    pin: str | None = Field(default=None, max_length=6)


class NewSessionRequest(BaseModel):
    private: bool = False
    pin: str | None = Field(default=None, max_length=6)
    language: str | None = None
    subject_user_id: str | None = None


class ResumeRequest(BaseModel):
    # The PIN alone identifies the chat among the caller's own private
    # sessions; session_id stays accepted so existing callers keep working.
    pin: str = Field(min_length=6, max_length=6)
    session_id: str | None = None


def _session_summary(session: ChatSession) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "is_private": session.is_private,
        "message_count": session.message_count,
        "language": session.language,
        "last_message_at": session.last_message_at,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
    }


# --------------------------------------------------------------------------
# Session management
# --------------------------------------------------------------------------
@router.get("/sessions")
def list_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=40, le=100),
) -> dict:
    """Visible conversation history. Private sessions never appear here."""
    chat_store.purge_expired(db)
    sessions = chat_store.list_sessions(db, current_user.id, limit=limit)
    return {"sessions": [_session_summary(s) for s in sessions]}


@router.post("/sessions", status_code=201)
def create_chat_session(
    payload: NewSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        session = chat_store.create_session(
            db,
            user_id=current_user.id,
            subject_user_id=payload.subject_user_id,
            language=payload.language or str(current_user.preferred_language),
            private=payload.private,
            pin=payload.pin,
        )
    except chat_store.ChatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_summary(session)


@router.get("/sessions/{session_id}")
def get_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = chat_store.get_session(db, session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if session.is_private:
        # Even the owner cannot list a private transcript without the PIN;
        # the resume endpoint is the only way back in.
        raise HTTPException(
            status_code=403,
            detail="This is a private conversation. Enter its PIN to resume.",
        )
    return {
        **_session_summary(session),
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "meta": m.meta,
                "created_at": m.created_at,
            }
            for m in session.messages
        ],
    }


@router.post("/sessions/resume")
def resume_private_session(
    payload: ResumeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        if payload.session_id:
            # A specific conversation was named; the PIN still has to be the
            # person's own, and the session still has to belong to them.
            chat_store.verify_private_pin(db, current_user.id, payload.pin)
            session = chat_store.get_session(
                db, payload.session_id, current_user.id
            )
            if session is None or not session.is_private:
                raise chat_store.ChatError("That private chat is no longer available.")
        else:
            session = chat_store.resume_private_by_pin(
                db, current_user.id, payload.pin
            )
    except chat_store.ChatError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return {
        **_session_summary(session),
        "messages": chat_store.private_buffer.read(session.id),
    }


@router.delete("/sessions/{session_id}")
def delete_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    session = chat_store.get_session(db, session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    cag.memory_store.forget(session.id)
    chat_store.destroy(db, session)
    return {"deleted": True, "id": session_id}


# --------------------------------------------------------------------------
# Turn execution
# --------------------------------------------------------------------------
def _prepare(db: Session, user: User, payload: AgentRequest) -> tuple[dict, ChatSession]:
    """Resolve scope, load or create the session, and build graph state."""
    try:
        scope = resolve_scope(db, user, payload.subject_user_id)
    except ToolDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    session: ChatSession | None = None
    if payload.session_id:
        session = chat_store.get_session(db, payload.session_id, user.id)
    if session is None:
        try:
            session = chat_store.create_session(
                db,
                user_id=user.id,
                subject_user_id=payload.subject_user_id,
                language=payload.language or str(user.preferred_language),
                private=payload.private,
                pin=payload.pin,
            )
        except chat_store.ChatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    history = chat_store.load_history(db, session)

    # The full clinical context is assembled here, but the PHI boundary inside
    # each agent minimises it per route before any of it reaches a prompt.
    context = build_patient_context(db, scope["subject_user_id"])
    scope["session_id"] = session.id

    # Durable facts from earlier conversations, so the assistant does not
    # re-ask what it was told last week. Private sessions read nothing.
    if not session.is_private:
        remembered = memory.recall(db, scope["subject_user_id"])
        if remembered:
            context["remembered"] = [f"{m.key.replace('_', ' ')}: {m.value}" for m in remembered]

    state = {
        "user_text": payload.message.strip(),
        "language": payload.language or session.language,
        "user_id": user.id,
        "role": str(user.role),
        "session_id": session.id,
        "confidential": session.is_private,
        "history": history,
        "scope": scope,
        "safe_context": context,
        "agent_outputs": [],
        "trace": [],
    }
    return state, session


def _audit(db: Session, user: User, state: dict, result: dict) -> None:
    """Record that a turn happened — never what was said.

    Private sessions are audited too: the fact of access is a security record,
    and it contains no content either way.
    """
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
                "cache_hit": bool((result.get("cag") or {}).get("hit")),
                "private": bool(state.get("confidential")),
            },
        )
    )
    db.commit()


def _response(state: dict, result: dict, *, latency_ms: int) -> dict:
    trace = result.get("trace") or []
    # Whichever component actually wrote the words the patient reads.
    provider = next(
        (
            entry.get("source")
            for entry in reversed(trace)
            if entry.get("node") in ("merge", "cache_lookup")
            or str(entry.get("node", "")).endswith("_agent")
        ),
        "deterministic",
    )
    return {
        "session_id": state["session_id"],
        "answer": result.get("answer", ""),
        "routes": [r.get("route") for r in (result.get("routes") or [])],
        "citations": result.get("citations", []),
        "suggestions": result.get("suggestions", []),
        "guard": result.get("guard", {}),
        "consult": result.get("consult", {}),
        "cache": result.get("cag", {"hit": False}),
        "trace": trace,
        "latency_ms": latency_ms,
        "provider": provider,
        "private": bool(state.get("confidential")),
        "structured": {
            output["route"]: output.get("structured", {})
            for output in (result.get("agent_outputs") or [])
        },
    }


def _persist(db: Session, session: ChatSession, state: dict, response: dict) -> None:
    # Learn durable facts *after* answering, so extraction never adds latency
    # to the reply the patient is waiting for.
    memory.learn_from_turn(
        db,
        state["scope"]["subject_user_id"],
        text=state["user_text"],
        session_id=session.id,
        confidential=session.is_private,
    )
    chat_store.append(db, session, role="user", content=state["user_text"])
    chat_store.append(
        db,
        session,
        role="assistant",
        content=response["answer"],
        meta={
            "routes": response["routes"],
            "provider": response["provider"],
            "latency_ms": response["latency_ms"],
            "cache_hit": bool(response["cache"].get("hit")),
            "guard": response["guard"],
        },
    )


@router.post("/chat")
def agent_chat(
    payload: AgentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Synchronous turn. Same contract as the stream's final event."""
    started = time.perf_counter()
    state, session = _prepare(db, current_user, payload)
    set_session(db)
    try:
        result = dict(agent_graph.invoke(state))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent graph failed")
        raise HTTPException(
            status_code=500, detail="The assistant could not complete that request."
        ) from exc

    response = _response(
        state, result, latency_ms=int((time.perf_counter() - started) * 1000)
    )
    _persist(db, session, state, response)
    _audit(db, current_user, state, result)
    return response


@router.post("/stream")
async def agent_stream(
    payload: AgentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream real node events as the graph executes."""
    started = time.perf_counter()
    state, session = _prepare(db, current_user, payload)
    session_id = session.id

    async def events() -> AsyncIterator[str]:
        # Open the stream immediately so proxies don't buffer the first chunk.
        yield ": open\n\n"
        yield _sse({
            "type": "session",
            "session_id": session_id,
            "private": bool(state.get("confidential")),
        })
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

            yield _sse({
                "type": "final",
                **_response(
                    state, final,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
            })
        except Exception:  # noqa: BLE001
            logger.exception("Agent stream failed")
            yield _sse({"type": "error", "detail": "The assistant failed."})
        finally:
            # Persist and audit on a private session: the request-scoped one
            # may already be closed by the time the stream finishes.
            write_db = SessionLocal()
            try:
                stored = chat_store.get_session(write_db, session_id, current_user.id)
                if stored is not None and final.get("answer"):
                    _persist(
                        write_db, stored, state,
                        _response(
                            state, final,
                            latency_ms=int((time.perf_counter() - started) * 1000),
                        ),
                    )
                _audit(write_db, current_user, state, final)
            except Exception:  # noqa: BLE001
                logger.warning("Agent persistence/audit failed", exc_info=True)
            finally:
                write_db.close()

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


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
# A memory store the patient cannot inspect is a surveillance feature. These
# three endpoints exist so "what does it know about me" and "forget that" are
# always answerable.
@router.get("/memory")
def list_memory(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Everything the assistant durably remembers, with its provenance."""
    memories = memory.recall(db, current_user.id, limit=50)
    return {
        "memories": [
            {
                "id": m.id,
                "kind": m.kind,
                "key": m.key,
                "label": m.key.replace("_", " "),
                "value": m.value,
                "confidence": round(m.confidence, 2),
                "learned_from": m.source_quote,
                "expires_at": m.expires_at,
                "updated_at": m.updated_at,
            }
            for m in memories
        ],
    }


@router.delete("/memory/{memory_id}")
def delete_memory(
    memory_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not memory.forget(db, current_user.id, memory_id):
        raise HTTPException(status_code=404, detail="That memory was not found.")
    return {"deleted": True, "id": memory_id}


@router.delete("/memory")
def clear_memory(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return {"deleted": memory.forget_all(db, current_user.id)}


@router.get("/status")
def agent_status(current_user: User = Depends(get_current_user)) -> dict:
    """Live agent configuration, including the honest privacy caveat."""
    status = orchestrator_status()
    return {
        "orchestrator": status,
        "graph": topology(),
        "consultation": consult.status(),
        "privacy": egress_policy_note(gemini_free_tier=status["llm_available"]),
    }
