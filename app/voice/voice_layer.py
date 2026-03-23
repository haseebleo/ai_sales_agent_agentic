"""
Voice Layer — STT, TTS, and Interruption Handling
Supports Deepgram, OpenAI Whisper, ElevenLabs, OpenAI TTS, and Cartesia.

Interruption Architecture:
- TTS audio is streamed in small chunks
- A VAD monitor runs concurrently, watching the microphone
- When speech is detected during TTS playback:
    1. TTS playback is cancelled via asyncio.Event
    2. The speaking flag is cleared
    3. STT captures the interruption utterance
    4. Agent processes the interruption with interrupted=True flag
- Anti-overlap: a mutex prevents simultaneous TTS + new response generation

Pipecat / LiveKit Note:
- When using the Pipecat pipeline (pipecat_pipeline.py), interruption detection
  is handled natively by Pipecat's frame processor chain + LiveKit transport.
  The classes in this file are used for the REST/WS fallback path.

Real-time Flow:
  mic → VAD → STT → Agent → TTS chunks → speaker
              ↑ interruption detected → cancel TTS → back to STT
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from io import BytesIO
from typing import Optional

from app.core.config import STTProvider, TTSProvider, settings

logger = logging.getLogger("trango_agent.voice")


# ── STT Abstraction ───────────────────────────────────────────────────────────

class STTBase(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000, mime_type: str = "audio/webm") -> str:
        """Transcribe audio bytes → text string."""

    @abstractmethod
    async def stream_transcribe(
        self, audio_stream: AsyncGenerator[bytes, None]
    ) -> AsyncGenerator[str, None]:
        """Streaming STT — yields partial transcripts."""


class DeepgramSTT(STTBase):
    def __init__(self) -> None:
        from deepgram import DeepgramClient
        self._client = DeepgramClient(api_key=settings.DEEPGRAM_API_KEY)
        self._model = settings.DEEPGRAM_STT_MODEL
        logger.info(f"Deepgram STT ready — model: {self._model}")

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000, mime_type: str = "audio/webm") -> str:
        from deepgram import PrerecordedOptions
        options = PrerecordedOptions(
            model=self._model,
            smart_format=True,
            punctuate=True,
            language="en",
        )

        # Use caller-provided mime type, validate with magic bytes as fallback
        mimetype = mime_type
        if audio_bytes[:4] == b'\x1aE\xdf\xa3':
            mimetype = "audio/webm"
        elif len(audio_bytes) > 8 and audio_bytes[4:8] == b'ftyp':
            mimetype = "audio/mp4"

        logger.info(f"STT: {len(audio_bytes)} bytes, mimetype={mimetype}")

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.listen.rest.v("1").transcribe_file(
                {"buffer": audio_bytes, "mimetype": mimetype},
                options,
            ),
        )
        try:
            channels = response.results.channels
            if channels and channels[0].alternatives:
                return (channels[0].alternatives[0].transcript or "").strip()
        except Exception:
            pass
        return ""

    async def stream_transcribe(
        self, audio_stream: AsyncGenerator[bytes, None]
    ) -> AsyncGenerator[str, None]:
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        if chunks:
            full_audio = b"".join(chunks)
            text = await self.transcribe(full_audio)
            if text:
                yield text


class OpenAIWhisperSTT(STTBase):
    def __init__(self) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        logger.info("OpenAI Whisper STT ready")

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000, mime_type: str = "audio/webm") -> str:
        ext = "mp4" if "mp4" in mime_type else "webm"
        audio_file = BytesIO(audio_bytes)
        audio_file.name = f"audio.{ext}"
        response = await self._client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text",
            language="en",
        )
        return str(response).strip()

    async def stream_transcribe(
        self, audio_stream: AsyncGenerator[bytes, None]
    ) -> AsyncGenerator[str, None]:
        """Whisper doesn't support true streaming — buffer and transcribe."""
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        if chunks:
            full_audio = b"".join(chunks)
            text = await self.transcribe(full_audio)
            yield text


# ── TTS Abstraction ───────────────────────────────────────────────────────────

class TTSBase(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesize text → raw audio bytes (PCM/MP3)."""

    @abstractmethod
    async def stream_synthesize(
        self, text: str, stop_event: asyncio.Event
    ) -> AsyncGenerator[bytes, None]:
        """
        Streaming TTS — yields audio chunks.
        stop_event: set this to interrupt playback mid-stream.
        """


class CartesiaTTS(TTSBase):
    """
    Cartesia TTS — ultra-low-latency streaming TTS.
    Output: pcm_s16le (raw 16-bit PCM) at 16kHz
    """

    def __init__(self) -> None:
        try:
            import cartesia
            self._client = cartesia.Cartesia(api_key=settings.CARTESIA_API_KEY)
        except ImportError:
            logger.warning("cartesia package not installed, falling back to OpenAI TTS")
            raise
        self._voice_id = settings.CARTESIA_VOICE_ID
        self._model_id = settings.CARTESIA_MODEL_ID
        self._output_format = {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": 16000,
        }
        logger.info(f"Cartesia TTS ready — voice: {self._voice_id}, model: {self._model_id}")

    async def synthesize(self, text: str) -> bytes:
        loop = asyncio.get_running_loop()

        def _sync():
            response = self._client.tts.sse(
                model_id=self._model_id,
                transcript=text,
                voice_id=self._voice_id,
                output_format=self._output_format,
                stream=False,
            )
            return response["audio"]

        return await loop.run_in_executor(None, _sync)

    async def stream_synthesize(
        self, text: str, stop_event: asyncio.Event
    ) -> AsyncGenerator[bytes, None]:
        import queue
        import threading

        chunk_queue: queue.Queue = queue.Queue()
        SENTINEL = object()

        def _stream_thread():
            try:
                for chunk in self._client.tts.sse(
                    model_id=self._model_id,
                    transcript=text,
                    voice_id=self._voice_id,
                    output_format=self._output_format,
                    stream=True,
                ):
                    if stop_event.is_set():
                        return
                    if "audio" in chunk:
                        chunk_queue.put(chunk["audio"])
            except Exception as exc:
                chunk_queue.put(exc)
            finally:
                chunk_queue.put(SENTINEL)

        thread = threading.Thread(target=_stream_thread, daemon=True)
        thread.start()

        loop = asyncio.get_running_loop()
        while True:
            if stop_event.is_set():
                logger.info("Cartesia TTS interrupted")
                return

            try:
                chunk = await loop.run_in_executor(None, lambda: chunk_queue.get(timeout=0.5))
            except Exception:
                if stop_event.is_set():
                    return
                continue

            if chunk is SENTINEL:
                break
            if isinstance(chunk, Exception):
                logger.warning(f"Cartesia TTS error: {chunk}")
                break
            yield chunk


class ElevenLabsTTS(TTSBase):
    def __init__(self) -> None:
        from elevenlabs.client import AsyncElevenLabs  # noqa: F811
        self._client = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
        self._voice_id = settings.ELEVENLABS_VOICE_ID
        self._model_id = settings.ELEVENLABS_MODEL_ID
        logger.info(f"ElevenLabs TTS ready — voice: {self._voice_id}")

    async def synthesize(self, text: str) -> bytes:
        audio_iter = await self._client.text_to_speech.convert(
            voice_id=self._voice_id,
            text=text,
            model_id=self._model_id,
        )
        chunks = []
        async for chunk in audio_iter:
            chunks.append(chunk)
        return b"".join(chunks)

    async def stream_synthesize(
        self, text: str, stop_event: asyncio.Event
    ) -> AsyncGenerator[bytes, None]:
        audio_stream = await self._client.text_to_speech.stream(
            voice_id=self._voice_id,
            text=text,
            model_id=self._model_id,
        )
        async for chunk in audio_stream:
            if stop_event.is_set():
                logger.info("TTS stream interrupted by barge-in")
                return
            yield chunk
            await asyncio.sleep(0)


class OpenAITTS(TTSBase):
    def __init__(self) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._voice = settings.OPENAI_TTS_VOICE
        logger.info(f"OpenAI TTS ready — voice: {self._voice}")

    async def synthesize(self, text: str) -> bytes:
        response = await self._client.audio.speech.create(
            model="tts-1",
            voice=self._voice,
            input=text,
            response_format="mp3",
        )
        return response.content

    async def stream_synthesize(
        self, text: str, stop_event: asyncio.Event
    ) -> AsyncGenerator[bytes, None]:
        async with self._client.audio.speech.with_streaming_response.create(
            model="tts-1-hd",
            voice=self._voice,
            input=text,
            response_format="mp3",
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                if stop_event.is_set():
                    logger.info("OpenAI TTS stream interrupted")
                    return
                yield chunk


class DeepgramTTS(TTSBase):
    """Deepgram Aura — ultra-low-latency TTS, best for real-time voice."""

    def __init__(self) -> None:
        import httpx
        self._api_key = settings.DEEPGRAM_API_KEY
        self._model = settings.DEEPGRAM_TTS_MODEL
        self._http = httpx.AsyncClient(timeout=30.0)
        logger.info(f"Deepgram TTS ready — model: {self._model}")

    async def synthesize(self, text: str) -> bytes:
        response = await self._http.post(
            "https://api.deepgram.com/v1/speak",
            params={"model": self._model},
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"text": text},
        )
        response.raise_for_status()
        return response.content

    async def stream_synthesize(
        self, text: str, stop_event: asyncio.Event
    ) -> AsyncGenerator[bytes, None]:
        async with self._http.stream(
            "POST",
            "https://api.deepgram.com/v1/speak",
            params={"model": self._model},
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"text": text},
        ) as response:
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if stop_event.is_set():
                    logger.info("Deepgram TTS stream interrupted")
                    return
                yield chunk


# ── Interruption Handler ──────────────────────────────────────────────────────

class InterruptionHandler:
    """
    Manages the barge-in / interruption lifecycle.
    
    When TTS is playing, the VAD monitor watches incoming audio.
    If speech is detected above threshold, barge_in() is called:
      - stop_event is set → TTS generator exits
      - is_speaking becomes False
      - The new utterance is routed to STT → agent
    """

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._is_speaking = False
        self._speaking_lock = asyncio.Lock()

    @property
    def stop_event(self) -> asyncio.Event:
        return self._stop_event

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    async def start_speaking(self) -> None:
        async with self._speaking_lock:
            self._stop_event.clear()
            self._is_speaking = True

    async def stop_speaking(self) -> None:
        async with self._speaking_lock:
            self._is_speaking = False

    async def barge_in(self) -> None:
        """Called when user starts speaking during TTS playback."""
        logger.info("Barge-in detected — stopping TTS")
        self._stop_event.set()
        self._is_speaking = False


class VADMonitor:
    """
    Voice Activity Detection monitor.
    In production with Pipecat, VAD is handled natively by the pipeline.
    This implementation is used for the REST/WS fallback path.
    """

    def __init__(
        self,
        interruption_handler: InterruptionHandler,
        energy_threshold: float = 500.0,
        silence_frames: int = 8,
    ) -> None:
        self._ih = interruption_handler
        self._energy_threshold = energy_threshold
        self._silence_frames = silence_frames

    async def monitor(self, audio_stream: AsyncGenerator[bytes, None]) -> None:
        import array
        silent_count = 0
        speaking = False

        async for chunk in audio_stream:
            if not chunk:
                continue
            try:
                samples = array.array("h", chunk)
                energy = (sum(s * s for s in samples) / len(samples)) ** 0.5
            except Exception:
                continue

            if energy > self._energy_threshold:
                silent_count = 0
                if not speaking:
                    speaking = True
                    if self._ih.is_speaking:
                        await self._ih.barge_in()
            else:
                silent_count += 1
                if silent_count > self._silence_frames:
                    speaking = False


# ── Factories ─────────────────────────────────────────────────────────────────

_stt_instance: STTBase | None = None
_tts_instance: TTSBase | None = None


def get_stt() -> STTBase:
    global _stt_instance
    if _stt_instance is None:
        if settings.STT_PROVIDER == STTProvider.OPENAI_WHISPER:
            _stt_instance = OpenAIWhisperSTT()
        else:
            try:
                _stt_instance = DeepgramSTT()
            except (ImportError, Exception) as e:
                logger.warning(f"Deepgram STT unavailable ({e}), falling back to OpenAI Whisper")
                _stt_instance = OpenAIWhisperSTT()
    return _stt_instance


def get_tts() -> TTSBase:
    global _tts_instance
    if _tts_instance is None:
        if settings.TTS_PROVIDER == TTSProvider.CARTESIA:
            try:
                _tts_instance = CartesiaTTS()
            except (ImportError, Exception) as e:
                logger.warning(f"Cartesia TTS unavailable ({e}), falling back to Deepgram TTS")
                _tts_instance = DeepgramTTS()
        elif settings.TTS_PROVIDER == TTSProvider.OPENAI_TTS:
            _tts_instance = OpenAITTS()
        elif settings.TTS_PROVIDER == TTSProvider.DEEPGRAM_TTS:
            _tts_instance = DeepgramTTS()
        else:
            try:
                _tts_instance = ElevenLabsTTS()
            except (ImportError, Exception) as e:
                logger.warning(f"ElevenLabs TTS unavailable ({e}), falling back to Deepgram TTS")
                _tts_instance = DeepgramTTS()
    return _tts_instance
