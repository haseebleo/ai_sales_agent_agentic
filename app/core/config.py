"""
Trango Tech AI Sales Agent — Central Configuration
All runtime settings, provider selection, and feature flags live here.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    DUAL = "dual"  # Ollama for early states, Gemini for high-reasoning states


class STTProvider(str, Enum):
    DEEPGRAM = "deepgram"
    OPENAI_WHISPER = "openai_whisper"
    ASSEMBLYAI = "assemblyai"


class TTSProvider(str, Enum):
    ELEVENLABS = "elevenlabs"
    OPENAI_TTS = "openai_tts"
    DEEPGRAM_TTS = "deepgram_tts"
    CARTESIA = "cartesia"


class VectorStoreProvider(str, Enum):
    CHROMA = "chroma"
    QDRANT = "qdrant"


class Settings(BaseSettings):
    # ── App Identity ──────────────────────────────────────────────────────────
    APP_NAME: str = "Trango Tech AI Sales Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Provider Selection ────────────────────────────────────────────────────
    LLM_PROVIDER: LLMProvider = LLMProvider.DUAL
    STT_PROVIDER: STTProvider = STTProvider.DEEPGRAM
    TTS_PROVIDER: TTSProvider = TTSProvider.CARTESIA
    VECTOR_STORE: VectorStoreProvider = VectorStoreProvider.CHROMA

    # ── Dual-LLM Routing Feature Flag ────────────────────────────────────────
    # When true, Ollama handles Discovery/Qualification and Gemini handles
    # Recommendation, Objection, Pricing, Closing, and Lead Capture states.
    DUAL_LLM_ROUTING: bool = True

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_TTS_VOICE: str = "nova"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-opus-4-5"

    # ── Ollama (self-hosted, M4 MacBook optimized) ───────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # ── Google Gemini ─────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ── Deepgram ──────────────────────────────────────────────────────────────
    DEEPGRAM_API_KEY: str = ""
    DEEPGRAM_STT_MODEL: str = "nova-2"
    DEEPGRAM_TTS_MODEL: str = "aura-asteria-en"

    # ── Cartesia TTS ─────────────────────────────────────────────────────────
    CARTESIA_API_KEY: str = ""
    # "British Reading Lady" (warm, professional): 79a125e8-cd45-4c13-8a67-188112f4dd22
    # "Sarah" (neutral American female):           a0e99841-438c-4a64-b679-ae501e7d6091
    CARTESIA_VOICE_ID: str = "a0e99841-438c-4a64-b679-ae501e7d6091"
    CARTESIA_MODEL_ID: str = "sonic-english"

    # ── ElevenLabs (legacy / fallback) ───────────────────────────────────────
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = "EXAVITQu4vr4xnSDxMaL"  # "Bella" — warm, professional
    ELEVENLABS_MODEL_ID: str = "eleven_turbo_v2_5"

    # ── AssemblyAI ───────────────────────────────────────────────────────────
    ASSEMBLYAI_API_KEY: str = ""

    # ── LiveKit (Pipecat transport) ────────────────────────────────────────────
    LIVEKIT_URL: str = ""                 # e.g. wss://your-project.livekit.cloud
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""

    # ── Vector Store: Chroma ──────────────────────────────────────────────────
    CHROMA_PERSIST_DIR: str = "./data/chroma_db"
    CHROMA_COLLECTION: str = "trango_knowledge"

    # ── Vector Store: Qdrant ─────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "trango_knowledge"

    # ── RAG Settings ──────────────────────────────────────────────────────────
    RAG_TOP_K: int = 5
    RAG_SCORE_THRESHOLD: float = 0.35
    RAG_CHUNK_SIZE: int = 400
    RAG_CHUNK_OVERLAP: int = 60
    KB_FILE_PATH: str = "./kb/knowledge_base.xlsx"
    KB_VERSION_FILE: str = "./data/kb_version.json"

    # ── Lead Output ───────────────────────────────────────────────────────────
    LEADS_FILE_PATH: str = "./data/leads.xlsx"
    LEAD_SHEET_NAME: str = "LeadData"

    # ── Conversation / Session ────────────────────────────────────────────────
    SESSION_TIMEOUT_SECONDS: int = 1800
    MAX_HISTORY_TURNS: int = 20

    # ── Voice / Interruption ──────────────────────────────────────────────────
    VAD_SILENCE_THRESHOLD_MS: int = 800
    BARGE_IN_ENABLED: bool = True
    TTS_CHUNK_DURATION_MS: int = 100
    AUDIO_SAMPLE_RATE: int = 16000

    # ── Lead Scoring Weights ──────────────────────────────────────────────────
    LEAD_SCORE_BUDGET_WEIGHT: float = 0.25
    LEAD_SCORE_TIMELINE_WEIGHT: float = 0.20
    LEAD_SCORE_AUTHORITY_WEIGHT: float = 0.25
    LEAD_SCORE_NEED_WEIGHT: float = 0.20
    LEAD_SCORE_FIT_WEIGHT: float = 0.10

    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: list[str] = ["*"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
