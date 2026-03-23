"""
Pipecat / LiveKit Voice Pipeline
Implements real-time, interruption-aware voice AI using the Pipecat framework.

Architecture:
    LiveKitTransport (mic in)
        → DeepgramSTTService        (nova-2 streaming transcription)
        → SalesAgentProcessor       (custom FrameProcessor: wraps SalesAgent)
        → CartesiaTTSService        (sonic-english, lowest latency)
        → LiveKitTransport (audio out)

Interruption / Barge-in:
    Pipecat handles this natively. When the user speaks while the TTS audio
    frame is in-flight, Pipecat's internal frame processor cancels the downstream
    TTS frame and re-routes the new TranscriptionFrame to SalesAgentProcessor.
    No manual stop_event management needed — this is the key advantage over raw WebSockets.

Usage:
    room = await start_pipecat_agent(session_id="abc-123")
    token = generate_livekit_token(room_name=room, participant="user")
    # Frontend connects to LiveKit room using the token
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from typing import Optional

from app.agents.sales_agent import SalesAgent
from app.agents.session_manager import get_session_manager
from app.core.config import settings

logger = logging.getLogger("trango_agent.pipecat")


# ── Custom Pipecat SalesAgent Frame Processor ─────────────────────────────────

class SalesAgentProcessor:
    """
    Wraps the SalesAgent orchestrator as a Pipecat-compatible processor.
    Receives text frames from Deepgram STT, streams tokens to the TTS service.
    
    Note: Pipecat's BaseProcessor interface is used at runtime.
    The class is defined here to be imported into the pipeline function.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._agent = SalesAgent()
        logger.info(f"SalesAgentProcessor initialized for session {session_id[:8]}")

    async def process(self, text: str, interrupted: bool = False) -> str:
        """Process a user utterance and return the full agent response."""
        session = await get_session_manager().get_or_create(self._session_id)
        response = await self._agent.process_message(
            session=session,
            user_message=text,
            interrupted=interrupted,
        )
        return response

    async def stream(self, text: str, interrupted: bool = False):
        """Stream agent response tokens (for Pipecat's text-to-TTS pipeline)."""
        session = await get_session_manager().get_or_create(self._session_id)
        async for token in self._agent.stream_response(
            session=session,
            user_message=text,
            interrupted=interrupted,
        ):
            yield token


# ── Pipeline Builder ──────────────────────────────────────────────────────────

async def build_pipecat_pipeline(session_id: str, room_name: str):
    """
    Construct and return a Pipecat pipeline for a LiveKit voice call.

    Returns the pipeline task (asyncio.Task) — caller awaits it or cancels it.
    """
    try:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.services.deepgram import DeepgramSTTService
        from pipecat.services.cartesia import CartesiaTTSService
        from pipecat.transports.services.livekit import LiveKitTransport, LiveKitParams
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
        from pipecat.frames.frames import TextFrame, TranscriptionFrame, EndFrame
        from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
    except ImportError as e:
        logger.error(f"Pipecat import failed: {e}. Run: pip install pipecat-ai[livekit,deepgram,cartesia]")
        raise

    agent_processor = SalesAgentProcessor(session_id)

    from pipecat.audio.vad.vad_analyzer import VADParams

    # ── LiveKit Transport ────────────────────────────────────────────────────────
    transport = LiveKitTransport(
        url=settings.LIVEKIT_URL,
        token=_generate_agent_token(room_name),
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=False,
        ),
    )

    # ── Deepgram STT ─────────────────────────────────────────────────────────────
    from deepgram import LiveOptions
    stt = DeepgramSTTService(
        api_key=settings.DEEPGRAM_API_KEY,
        live_options=LiveOptions(
            model=settings.DEEPGRAM_STT_MODEL,
            language="en-US",
            smart_format=True,
            punctuate=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms="1000",
        ),
    )

    # ── Cartesia TTS ─────────────────────────────────────────────────────────────
    tts = CartesiaTTSService(
        api_key=settings.CARTESIA_API_KEY,
        voice_id=settings.CARTESIA_VOICE_ID,
        model_id=settings.CARTESIA_MODEL_ID,
    )

    # ── Custom SalesAgent Frame Processor ────────────────────────────────────────
    class _AgentFrameProcessor(FrameProcessor):
        """
        Sits between Deepgram STT and Cartesia TTS.
        Receives TranscriptionFrames, sends TextFrames downstream.
        Now async so VAD interruptions can pass through instantly!
        """

        def __init__(self) -> None:
            super().__init__()
            self._task: Optional[asyncio.Task] = None

        async def process_frame(self, frame, direction: FrameDirection):
            from pipecat.frames.frames import UserStartedSpeakingFrame, UserStoppedSpeakingFrame, CancelFrame
            await super().process_frame(frame, direction)

            sess = session_id[:8] if session_id else "unknown"

            if isinstance(frame, UserStartedSpeakingFrame):
                logger.info(f"[{sess}] VAD: UserStartedSpeakingFrame (Interruption!)")
                # Cancel ongoing LLM generation
                current_task = self._task
                if current_task and not current_task.done():
                    current_task.cancel()
                    self._task = None
                # Ping downstream immediately to tell TTS to stop
                await self.push_frame(CancelFrame(), direction)
            
            elif isinstance(frame, UserStoppedSpeakingFrame):
                logger.debug(f"[{sess}] VAD: UserStoppedSpeakingFrame")

            elif frame.__class__.__name__ == "InterimTranscriptionFrame":
                if hasattr(frame, "text") and frame.text.strip():
                    logger.debug(f"[{sess}] Interim: {frame.text}")

            elif isinstance(frame, TranscriptionFrame):
                text = frame.text
                if not text.strip():
                    return
                logger.info(f"[{sess}] User said (FINAL): {text[:80]!r}")

                # Mirror user voice input to the UI chat
                try:
                    import json
                    if transport.room and transport.room.local_participant:
                        msg = json.dumps({"role": "user", "content": text}).encode("utf-8")
                        await transport.room.local_participant.publish_data(msg, reliable=True)
                except Exception as e:
                    logger.warning(f"Failed to publish user msg to chat: {e}")

                async def _handle_llm(text_input):
                    try:
                        response = await agent_processor.process(text=text_input, interrupted=False)
                        logger.info(f"[{sess}] Agent response ({len(response)} chars)")
                        
                        # Mirror AI voice output to the UI chat
                        try:
                            if transport.room and transport.room.local_participant:
                                msg = json.dumps({"role": "ai", "content": response}).encode("utf-8")
                                await transport.room.local_participant.publish_data(msg, reliable=True)
                        except Exception as e:
                            logger.warning(f"Failed to publish AI msg to chat: {e}")

                        await self.push_frame(TextFrame(text=response))
                    except asyncio.CancelledError:
                        logger.info(f"[{sess}] LLM generation interrupted by user")
                    except Exception as e:
                        logger.error(f"LLM Error: {e}")

                current_task = self._task
                if current_task and not current_task.done():
                    current_task.cancel()
                
                self._task = asyncio.create_task(_handle_llm(text))

            else:
                await self.push_frame(frame, direction)

    agent_fp = _AgentFrameProcessor()

    # ── Assemble Pipeline ─────────────────────────────────────────────────────────
    pipeline = Pipeline([
        transport.input(),      # LiveKit microphone audio in
        stt,                    # Deepgram: audio → transcript
        agent_fp,               # SalesAgent: transcript → response text
        tts,                    # Cartesia: text → audio chunks
        transport.output(),     # LiveKit audio out to participant
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=settings.BARGE_IN_ENABLED,
            enable_metrics=True,
        ),
    )

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info(f"First participant joined to room {room_name}")
        await task.queue_frames([
            TextFrame("Hi there! I'm Alex. Let's build something amazing. How can I help you today?")
        ])

    @transport.event_handler("on_participant_disconnected")
    async def on_disconnect(transport, participant):
        logger.info(f"Participant disconnected from room {room_name}")
        await task.queue_frame(EndFrame())

    return task


# ── LiveKit Token Generation ──────────────────────────────────────────────────

def generate_livekit_token(
    room_name: str,
    participant_name: str,
    ttl_seconds: int = 3600,
) -> str:
    """
    Generate a LiveKit JWT for a user participant to join a voice room.
    Intended for the POST /livekit/token REST endpoint.
    """
    try:
        from livekit.api import AccessToken, VideoGrants
        token = (
            AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            .with_identity(participant_name)
            .with_name(participant_name)
            .with_ttl(datetime.timedelta(seconds=ttl_seconds))
            .with_grants(VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            ))
            .to_jwt()
        )
        return token
    except ImportError:
        logger.error("livekit-api not installed. Run: pip install livekit-api")
        raise


def _generate_agent_token(room_name: str) -> str:
    """Generate a LiveKit token for the agent (server-side participant)."""
    try:
        from livekit.api import AccessToken, VideoGrants
        token = (
            AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            .with_identity("trango-agent")
            .with_name("Alex (Trango AI)")
            .with_ttl(datetime.timedelta(seconds=7200))
            .with_grants(VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            ))
            .to_jwt()
        )
        return token
    except ImportError:
        return ""


# ── Public API ───────────────────────────────────────────────────────────────

# Maps session_id → asyncio.Task (the running Pipecat pipeline)
_active_pipelines: dict[str, asyncio.Task] = {}


async def start_pipecat_agent(session_id: Optional[str] = None) -> tuple[str, str]:
    """
    Start a Pipecat voice agent for a new call.
    Returns (room_name, session_id).
    
    Called from POST /livekit/start REST endpoint.
    """
    from pipecat.pipeline.runner import PipelineRunner

    if session_id is None:
        session_id = str(uuid.uuid4())

    room_name = f"trango-{session_id[:8]}"
    logger.info(f"Starting Pipecat agent in room '{room_name}' (session={session_id[:8]})")

    task = await build_pipecat_pipeline(session_id=session_id, room_name=room_name)
    runner = PipelineRunner()

    # Run pipeline in background
    async def _run():
        try:
            await runner.run(task)
        except Exception as e:
            logger.error(f"Pipecat pipeline error (session={session_id[:8]}): {e}", exc_info=True)
        finally:
            _active_pipelines.pop(session_id, None)
            logger.info(f"Pipecat pipeline ended (session={session_id[:8]})")

    pipeline_task = asyncio.create_task(_run(), name=f"pipecat-{session_id[:8]}")
    _active_pipelines[session_id] = pipeline_task

    return room_name, session_id


async def stop_pipecat_agent(session_id: str) -> bool:
    """Cancel a running Pipecat pipeline. Returns True if found and cancelled."""
    task = _active_pipelines.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        logger.info(f"Cancelled Pipecat pipeline (session={session_id[:8]})")
        return True
    return False
