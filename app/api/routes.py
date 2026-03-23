"""
FastAPI Application — Trango Tech AI Sales Agent
REST endpoints + WebSocket for real-time voice conversation.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agents.sales_agent import SalesAgent
from app.agents.session_manager import SessionManager, get_session_manager
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.models import AgentState, LeadData, SessionMemory
from app.leads.excel_writer import LeadExcelWriter
from app.rag.indexer import run_ingestion
from app.rag.vector_store import get_vector_store
from app.voice.voice_layer import (
    InterruptionHandler,
    get_stt,
    get_tts,
)

logger = setup_logging(settings.LOG_LEVEL)

# ── Lifespan: startup / shutdown ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Trango Tech AI Sales Agent...")

    # Auto-index knowledge base on startup
    try:
        run_ingestion(settings.KB_FILE_PATH, force=False)
    except Exception as e:
        logger.warning(f"KB auto-index skipped: {e}")

    # Start session cleanup background task
    cleanup_task = asyncio.create_task(_session_cleanup_loop())

    logger.info("Agent ready.")
    yield

    cleanup_task.cancel()
    logger.info("Shutting down.")


async def _session_cleanup_loop():
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        sm = get_session_manager()
        await sm.cleanup_expired()


# ── App Init ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Trango Tech AI Sales Agent",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dependencies ──────────────────────────────────────────────────────────────

def get_agent() -> SalesAgent:
    return SalesAgent()


def get_lead_writer() -> LeadExcelWriter:
    return LeadExcelWriter()


# ── Request / Response Models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    interrupted: bool = False


class ChatResponse(BaseModel):
    response: str
    session_id: str
    agent_state: str
    lead_temperature: str
    qualification_score: float
    lead_saved: bool
    sources_used: list[str]


class IngestRequest(BaseModel):
    force: bool = False
    file_path: Optional[str] = None


class EndSessionRequest(BaseModel):
    session_id: str
    force_save_lead: bool = True


# ── Health Check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    store = get_vector_store()
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "kb_indexed": store.collection_count(),
        "llm_provider": settings.LLM_PROVIDER.value,
        "stt_provider": settings.STT_PROVIDER.value,
        "tts_provider": settings.TTS_PROVIDER.value,
        "active_sessions": get_session_manager().active_count(),
    }


# ── Text Chat Endpoint ────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    sm: SessionManager = Depends(get_session_manager),
    agent: SalesAgent = Depends(get_agent),
):
    """
    Primary text chat endpoint.
    Creates or resumes a session, processes the message through the full agent pipeline.
    """
    session = await sm.get_or_create(request.session_id)
    response = await agent.process_message(
        session=session,
        user_message=request.message,
        interrupted=request.interrupted,
    )
    return ChatResponse(
        response=response,
        session_id=session.session_id,
        agent_state=session.state.value,
        lead_temperature=session.qualification.temperature().value,
        qualification_score=round(session.qualification.overall_score(), 3),
        lead_saved=session.lead_saved,
        sources_used=session.retrieval_sources_used[-5:],
    )


# ── Streaming Text Chat ───────────────────────────────────────────────────────

@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    sm: SessionManager = Depends(get_session_manager),
    agent: SalesAgent = Depends(get_agent),
):
    """
    Server-Sent Events streaming endpoint.
    Useful for web frontend to show typing effect.
    """
    session = await sm.get_or_create(request.session_id)

    async def generate():
        yield f"data: {{\"session_id\": \"{session.session_id}\"}}\n\n"
        async for token in agent.stream_response(
            session=session,
            user_message=request.message,
            interrupted=request.interrupted,
        ):
            payload = json.dumps({"token": token})
            yield f"data: {payload}\n\n"
        # Final state update
        state_payload = json.dumps({
            "done": True,
            "agent_state": session.state.value,
            "lead_temperature": session.qualification.temperature().value,
            "qualification_score": round(session.qualification.overall_score(), 3),
        })
        yield f"data: {state_payload}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Voice: Upload Audio → Transcribe → Respond → TTS ──────────────────────────

@app.post("/voice/turn")
async def voice_turn(
    audio: UploadFile,
    session_id: Optional[str] = Query(default=None),
    interrupted: bool = Query(default=False),
    sm: SessionManager = Depends(get_session_manager),
    agent: SalesAgent = Depends(get_agent),
):
    """
    Single voice turn:
    1. Receive audio file upload
    2. Transcribe via STT
    3. Process through sales agent
    4. Return TTS audio + text response
    """
    audio_bytes = await audio.read()
    stt = get_stt()
    tts = get_tts()

    # Transcribe
    try:
        user_text = await stt.transcribe(audio_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"STT error: {e}")

    if not user_text.strip():
        raise HTTPException(status_code=400, detail="No speech detected in audio")

    # Agent
    session = await sm.get_or_create(session_id)
    response_text = await agent.process_message(
        session=session,
        user_message=user_text,
        interrupted=interrupted,
    )

    # TTS
    try:
        audio_response = await tts.synthesize(response_text)
        audio_b64 = base64.b64encode(audio_response).decode("utf-8")
    except Exception as e:
        logger.warning(f"TTS synthesis failed: {e} — returning text only")
        audio_b64 = ""

    return {
        "user_text": user_text,
        "response_text": response_text,
        "audio_base64": audio_b64,
        "session_id": session.session_id,
        "agent_state": session.state.value,
        "lead_temperature": session.qualification.temperature().value,
        "qualification_score": round(session.qualification.overall_score(), 3),
    }


# ── WebSocket: Real-Time Voice with Barge-In ──────────────────────────────────

@app.websocket("/ws/voice/{session_id}")
async def websocket_voice(
    websocket: WebSocket,
    session_id: str,
    sm: SessionManager = Depends(get_session_manager),
    agent: SalesAgent = Depends(get_agent),
):
    """
    Full-duplex WebSocket voice conversation with interruption support.

    Client → Server:
      {"type": "audio", "data": "<base64>"}             — audio chunk (WebM/Opus from MediaRecorder)
      {"type": "end_of_speech"}                          — user finished speaking
      {"type": "interrupt"}                              — explicit barge-in signal
      {"type": "text", "content": "hello"}               — text fallback
      {"type": "end_session"}                            — close and save lead

    Server → Client:
      {"type": "transcript", "text": "..."}              — STT result
      {"type": "token", "text": "..."}                   — streaming LLM token
      {"type": "audio_chunk", "data": "<base64_pcm>"}    — TTS audio (PCM s16le 16kHz)
      {"type": "response_done"}                          — all tokens + audio sent
      {"type": "interrupted"}                            — TTS was interrupted
      {"type": "state", ...}                             — state update
      {"type": "lead_saved", "lead_id": "..."}
      {"type": "error", "message": "..."}
    """
    await websocket.accept()
    session = await sm.get_or_create(session_id)
    ih = InterruptionHandler()
    stt = get_stt()
    tts = get_tts()

    audio_buffer: list[bytes] = []
    audio_has_header = False  # tracks if buffer starts with a valid WebM header
    tts_task: Optional[asyncio.Task] = None
    response_task: Optional[asyncio.Task] = None
    cancelled = False

    MIN_AUDIO_SIZE = 500  # ignore tiny fragments that can't contain speech

    logger.info(f"WS connected: {session_id[:8]}")

    async def _safe_send(data: dict) -> bool:
        try:
            await websocket.send_json(data)
            return True
        except Exception:
            return False

    async def _cancel_active_tasks():
        nonlocal cancelled
        cancelled = True
        if tts_task and not tts_task.done():
            tts_task.cancel()
        if response_task and not response_task.done():
            response_task.cancel()
        await ih.barge_in()
        await _safe_send({"type": "interrupted"})

    async def _process_speech(audio_data: bytes, mime_type: str = "audio/webm"):
        """Transcribe audio, get LLM response, stream TTS."""
        nonlocal tts_task, cancelled
        cancelled = False

        if len(audio_data) < MIN_AUDIO_SIZE:
            logger.debug(f"Audio too small ({len(audio_data)} bytes), skipping STT")
            return

        logger.info(f"[{session_id[:8]}] STT: {len(audio_data)} bytes, mime={mime_type}")

        try:
            user_text = await stt.transcribe(audio_data, mime_type=mime_type)
        except Exception as e:
            logger.error(f"STT failed: {e}", exc_info=True)
            await _safe_send({"type": "error", "message": f"Speech recognition failed. Try speaking louder or longer."})
            return

        if not user_text.strip():
            logger.debug("Empty transcription, skipping")
            return

        await _safe_send({"type": "transcript", "text": user_text})
        await _process_text(user_text, interrupted=session.was_interrupted)

    async def _process_text(user_text: str, interrupted: bool = False):
        """Stream LLM response + TTS for a text input."""
        nonlocal tts_task, cancelled
        cancelled = False

        full_response: list[str] = []
        try:
            async for token in agent.stream_response(
                session=session,
                user_message=user_text,
                interrupted=interrupted,
            ):
                if cancelled:
                    return
                await _safe_send({"type": "token", "text": token})
                full_response.append(token)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"LLM stream error: {e}", exc_info=True)
            await _safe_send({"type": "error", "message": f"Agent error: {e}"})
            return

        if cancelled:
            return

        response_text = "".join(full_response)

        # Stream TTS audio
        if response_text.strip():
            await ih.start_speaking()

            async def _stream_tts():
                try:
                    async for chunk in tts.stream_synthesize(response_text, ih.stop_event):
                        if cancelled:
                            return
                        audio_b64 = base64.b64encode(chunk).decode("utf-8")
                        await _safe_send({"type": "audio_chunk", "data": audio_b64})
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"TTS stream error: {e}")
                finally:
                    await ih.stop_speaking()
                    if not cancelled:
                        await _safe_send({"type": "response_done"})

            tts_task = asyncio.create_task(_stream_tts())

        # Send state update
        state_msg = {
            "type": "state",
            "agent_state": session.state.value,
            "lead_temperature": session.qualification.temperature().value,
            "qualification_score": round(session.qualification.overall_score(), 3),
            "lead_saved": session.lead_saved,
        }
        if session.lead.full_name:
            state_msg["lead_name"] = session.lead.full_name
        if session.lead.company_name:
            state_msg["lead_company"] = session.lead.company_name
        if session.lead.email:
            state_msg["lead_email"] = session.lead.email
        if session.lead.phone:
            state_msg["lead_phone"] = session.lead.phone
        if session.lead.interested_service:
            state_msg["lead_service"] = session.lead.interested_service
        if session.lead.estimated_budget:
            state_msg["lead_budget"] = session.lead.estimated_budget
        if session.lead.desired_timeline:
            state_msg["lead_timeline"] = session.lead.desired_timeline
        if session.lead.industry:
            state_msg["lead_industry"] = session.lead.industry
        if session.lead.country:
            state_msg["lead_country"] = session.lead.country
        if session.lead.recommended_package:
            state_msg["lead_package"] = session.lead.recommended_package
        await _safe_send(state_msg)

        if session.lead_saved and session.lead.lead_id:
            await _safe_send({"type": "lead_saved", "lead_id": session.lead.lead_id})

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            # ── Explicit interrupt from frontend ────────────────────────────
            if msg_type == "interrupt":
                logger.info(f"[{session_id[:8]}] Explicit interrupt received")
                await _cancel_active_tasks()
                audio_buffer.clear()
                audio_has_header = False
                continue

            # ── Complete audio file from frontend (primary path) ──────────
            if msg_type == "audio_complete":
                audio_data = base64.b64decode(msg["data"])
                mime = msg.get("mime", "audio/webm")

                if len(audio_data) < MIN_AUDIO_SIZE:
                    logger.debug(f"[{session_id[:8]}] audio_complete too small ({len(audio_data)}B)")
                    continue

                # Cancel any active TTS/response before starting new one
                if ih.is_speaking or (tts_task and not tts_task.done()):
                    await _cancel_active_tasks()

                response_task = asyncio.create_task(_process_speech(audio_data, mime_type=mime))
                continue

            # ── Audio chunk: accumulate (legacy/fallback path) ────────────
            if msg_type == "audio":
                chunk = base64.b64decode(msg["data"])
                if not audio_buffer:
                    audio_has_header = True
                audio_buffer.append(chunk)
                continue

            # ── End of speech (legacy/fallback path) ──────────────────────
            if msg_type == "end_of_speech":
                if not audio_buffer or not audio_has_header:
                    if audio_buffer and not audio_has_header:
                        logger.debug(f"[{session_id[:8]}] Discarding {len(audio_buffer)} headerless audio chunks")
                    audio_buffer.clear()
                    audio_has_header = False
                    continue

                full_audio = b"".join(audio_buffer)
                audio_buffer.clear()
                audio_has_header = False

                if ih.is_speaking or (tts_task and not tts_task.done()):
                    await _cancel_active_tasks()

                response_task = asyncio.create_task(_process_speech(full_audio))
                continue

            # ── Text message (no audio) ────────────────────────────────────
            if msg_type == "text":
                user_text = msg.get("content", "")
                if not user_text.strip():
                    continue
                if ih.is_speaking or (tts_task and not tts_task.done()):
                    await _cancel_active_tasks()
                await _safe_send({"type": "transcript", "text": user_text})
                response_task = asyncio.create_task(_process_text(user_text))
                continue

            # ── End session ────────────────────────────────────────────────
            if msg_type == "end_session":
                saved_lead = await agent.force_save_lead(session)
                await _safe_send({
                    "type": "session_ended",
                    "lead_id": saved_lead.lead_id if saved_lead else None,
                    "lead_temperature": session.qualification.temperature().value,
                })
                await sm.delete(session_id)
                break

    except WebSocketDisconnect:
        logger.info(f"WS disconnected: {session_id[:8]}")
        try:
            await agent.force_save_lead(session)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"WS error [{session_id[:8]}]: {e}", exc_info=True)
        await _safe_send({"type": "error", "message": str(e)})


# ── Session Management ────────────────────────────────────────────────────────

@app.get("/session/{session_id}")
async def get_session(
    session_id: str,
    sm: SessionManager = Depends(get_session_manager),
):
    session = await sm.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "state": session.state.value,
        "lead_temperature": session.qualification.temperature().value,
        "qualification_score": round(session.qualification.overall_score(), 3),
        "lead": session.lead.model_dump(),
        "turn_count": len(session.history),
        "lead_saved": session.lead_saved,
    }


@app.delete("/session/{session_id}")
async def end_session(
    session_id: str,
    force_save: bool = Query(default=True),
    sm: SessionManager = Depends(get_session_manager),
    agent: SalesAgent = Depends(get_agent),
):
    session = await sm.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    saved_lead = None
    if force_save:
        saved_lead = await agent.force_save_lead(session)

    await sm.delete(session_id)
    return {
        "status": "closed",
        "session_id": session_id,
        "lead_id": saved_lead.lead_id if saved_lead else None,
    }


# ── Leads ─────────────────────────────────────────────────────────────────────

@app.get("/leads")
async def get_leads(
    writer: LeadExcelWriter = Depends(get_lead_writer),
):
    """Return all captured leads as JSON."""
    return {"leads": writer.get_all_leads()}


@app.get("/leads/download")
async def download_leads():
    """Download the leads Excel file directly."""
    import os
    from fastapi.responses import FileResponse
    path = settings.LEADS_FILE_PATH
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No leads file yet")
    return FileResponse(
        path=path,
        filename="trango_tech_leads.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Knowledge Base Ingestion ──────────────────────────────────────────────────

@app.post("/ingest")
async def ingest_knowledge_base(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger knowledge base re-indexing.
    Use force=true to re-index even if KB hasn't changed.
    Can be called when knowledge_base.xlsx is updated.
    """
    file_path = request.file_path or settings.KB_FILE_PATH
    background_tasks.add_task(run_ingestion, file_path, request.force)
    return {
        "status": "ingestion_queued",
        "file": file_path,
        "force": request.force,
        "message": "Knowledge base re-indexing started in background",
    }


@app.get("/ingest/status")
async def ingest_status():
    """Check current KB index status."""
    from app.rag.ingestion import get_kb_version, load_version_record
    stored = load_version_record(settings.KB_VERSION_FILE)
    current = get_kb_version(settings.KB_FILE_PATH)
    store = get_vector_store()
    return {
        "kb_file": settings.KB_FILE_PATH,
        "current_kb_version": current,
        "indexed_version": stored.get("version", "not_indexed"),
        "indexed_at": stored.get("indexed_at"),
        "is_current": stored.get("version") == current,
        "document_count": store.collection_count(),
    }


# ── RAG Query (debug/testing) ─────────────────────────────────────────────────

class RAGQueryRequest(BaseModel):
    query: str
    top_k: int = 5


@app.post("/rag/query")
async def rag_query(request: RAGQueryRequest):
    """Debug endpoint: test RAG retrieval directly."""
    from app.rag.retrieval import retrieve
    chunks, strong = retrieve(request.query, top_k=request.top_k)
    return {
        "query": request.query,
        "strong_match": strong,
        "results": [
            {
                "text": c.text[:300] + "..." if len(c.text) > 300 else c.text,
                "score": round(c.score, 4),
                "metadata": c.metadata,
            }
            for c in chunks
        ],
    }


# ── LiveKit / Pipecat Voice Endpoints ────────────────────────────────────────

class LiveKitTokenRequest(BaseModel):
    room_name: str
    participant_name: str = "user"
    ttl_seconds: int = 3600


class LiveKitStartRequest(BaseModel):
    session_id: Optional[str] = None


@app.post("/livekit/token")
async def livekit_token(request: LiveKitTokenRequest):
    """
    Issue a LiveKit JWT for a participant to join a voice room.
    Frontend / phone system calls this first, then connects to LiveKit directly.
    """
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=503,
            detail="LiveKit credentials not configured. Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env",
        )
    try:
        from app.voice.pipecat_pipeline import generate_livekit_token
        token = generate_livekit_token(
            room_name=request.room_name,
            participant_name=request.participant_name,
            ttl_seconds=request.ttl_seconds,
        )
        return {
            "token": token,
            "url": settings.LIVEKIT_URL,
            "room_name": request.room_name,
        }
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"livekit-api not installed: {e}")
    except Exception as e:
        logger.error(f"LiveKit token error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/livekit/start")
async def livekit_start(request: LiveKitStartRequest):
    """
    Start a Pipecat/LiveKit voice agent for a new or existing session.
    Returns room name + participant token for the user's frontend to connect.
    """
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=503,
            detail="LiveKit credentials not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env",
        )
    try:
        from app.voice.pipecat_pipeline import generate_livekit_token, start_pipecat_agent
        room_name, session_id = await start_pipecat_agent(session_id=request.session_id)
        user_token = generate_livekit_token(room_name=room_name, participant_name="user")
        return {
            "session_id": session_id,
            "room_name": room_name,
            "token": user_token,
            "url": settings.LIVEKIT_URL,
            "message": "Pipecat voice agent started. Connect to LiveKit room using token + url.",
        }
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Pipecat or LiveKit not installed: {e}. Run: pip install 'pipecat-ai[livekit,deepgram,cartesia]' livekit-api",
        )
    except Exception as e:
        logger.error(f"LiveKit start error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/livekit/stop/{session_id}")
async def livekit_stop(session_id: str):
    """Stop a running Pipecat pipeline."""
    try:
        from app.voice.pipecat_pipeline import stop_pipecat_agent
        stopped = await stop_pipecat_agent(session_id)
        return {
            "session_id": session_id,
            "stopped": stopped,
            "message": "Pipeline cancelled." if stopped else "No active pipeline found.",
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Pipecat not installed")


@app.get("/livekit/status")
async def livekit_status():
    """Check how many Pipecat pipelines are currently running."""
    try:
        from app.voice.pipecat_pipeline import _active_pipelines
        return {
            "active_pipelines": len(_active_pipelines),
            "session_ids": list(_active_pipelines.keys()),
            "livekit_configured": bool(settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET),
        }
    except ImportError:
        return {"active_pipelines": 0, "livekit_configured": False}


# ── Frontend Mounting ─────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")

