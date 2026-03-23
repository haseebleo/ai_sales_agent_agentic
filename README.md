# Trango Tech AI Sales Agent

A production-grade, voice-first, agentic AI sales system for **Trango Tech** — a full-service software development agency. This system acts as a senior sales consultant: it listens to prospects via real-time voice, qualifies leads through a 9-state conversation funnel, recommends services grounded in a RAG knowledge base, handles objections, and captures structured lead data into Excel — all through a modern React web interface.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Architecture Overview](#architecture-overview)
3. [Tech Stack](#tech-stack)
4. [Conversation Workflow](#conversation-workflow)
5. [Folder Structure](#folder-structure)
6. [Quick Start](#quick-start)
7. [Frontend Development](#frontend-development)
8. [Environment Variables](#environment-variables)
9. [API Reference](#api-reference)
10. [WebSocket Protocol](#websocket-protocol)
11. [Knowledge Base Management](#knowledge-base-management)
12. [Lead Capture](#lead-capture)
13. [Deployment](#deployment)
14. [Running Tests](#running-tests)
15. [Model Recommendations](#model-recommendations)
16. [Future Improvements](#future-improvements)

---

## How It Works

The Trango AI Sales Agent is **not a FAQ chatbot** — it is an autonomous, goal-driven sales agent that conducts voice conversations with prospects. Here is the end-to-end flow:

### 1. User Opens the Web App
The prospect opens the React frontend in their browser and clicks **Start Call**. This does three things:
- Requests microphone permission
- Opens a WebSocket connection to the FastAPI backend (`/ws/voice/{session_id}`)
- Creates an `AudioContext` for playback and an `AnalyserNode` for voice activity detection (VAD)

### 2. User Speaks
The browser's `MediaRecorder` continuously captures audio from the microphone. A client-side **Voice Activity Detection** loop monitors the audio energy (RMS) via the Web Audio API `AnalyserNode`:
- When the user starts speaking, the VAD detects energy above a threshold
- When the user stops speaking (silence for ~800ms), the `MediaRecorder` is stopped
- The `onstop` event produces a single, complete audio blob (WebM/Opus format)
- This blob is Base64-encoded and sent to the backend as an `audio_complete` WebSocket message

### 3. Speech-to-Text (STT)
The backend receives the complete audio file and sends it to the configured STT provider:
- **Deepgram Nova-2** (default) — lowest latency, best for real-time
- **OpenAI Whisper** — highest accuracy, slightly higher latency

The transcribed text is sent back to the frontend as a `transcript` message and displayed in the conversation feed.

### 4. Agent Processing (State Machine + RAG + LLM)
The transcribed text flows through the **Sales Agent orchestrator**:

1. **State Machine Evaluation** — Based on keyword detection and conversation history, the agent determines the current sales funnel state (e.g., `discovery` → `qualification` → `recommendation`)
2. **RAG Retrieval** — The user's message is embedded and matched against the vector store (ChromaDB/Qdrant) to retrieve the top-K most relevant knowledge base chunks (pricing, packages, timelines, etc.)
3. **Prompt Assembly** — A system prompt is built from: persona definition + state-specific behavioral instructions + retrieved KB context + conversation history
4. **LLM Response Generation** — The assembled prompt is sent to the LLM for streaming completion. With **Dual-LLM Routing** (default):
   - **Early states** (greeting, discovery, qualification) → Ollama Llama 3.1 (local, free, fast)
   - **High-reasoning states** (recommendation, objection handling, pricing, closing, lead capture) → Google Gemini 2.5 Flash (cloud, strong reasoning)
5. **Lead Scoring** — After each turn, qualification scores are updated across 6 dimensions (need clarity, budget, authority, urgency, seriousness, fit)

### 5. Text-to-Speech (TTS)
As LLM tokens stream in, the complete response is sent to the TTS provider:
- **Cartesia Sonic** (default) — low-latency streaming, natural voice
- **ElevenLabs Turbo v2.5** — most human-like warmth
- **OpenAI TTS** / **Deepgram Aura** — alternative options

TTS audio chunks are Base64-encoded and streamed to the frontend via `audio_chunk` WebSocket messages.

### 6. Audio Playback
The frontend decodes incoming audio chunks, creates `AudioBuffer` objects via the Web Audio API, and queues them for gapless sequential playback. The call state indicator shows **"Agent Speaking"**.

### 7. Barge-In / Interruption Handling
If the user speaks while the agent is talking:
- Client-side VAD detects speech energy above the barge-in threshold
- An `interrupt` message is sent to the backend
- The backend cancels the active TTS stream and LLM generation
- The frontend clears its audio playback queue
- The `MediaRecorder` is restarted fresh for the new utterance
- Normal flow resumes from step 2

### 8. Lead Capture
When a lead reaches **warm** or **hot** temperature (qualification score ≥ 0.35), has provided an email, and the conversation has at least 8 turns:
- The LLM extracts structured lead fields (name, email, company, budget, timeline, etc.) via a dedicated extraction prompt
- The lead is scored across 6 dimensions using a weighted scoring model
- All 28 fields are written to `data/leads.xlsx` with duplicate prevention
- A `lead_saved` message is sent to the frontend

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BROWSER (React + Vite)                            │
│                                                                             │
│  ┌─────────────┐  ┌───────────────┐  ┌─────────────┐  ┌──────────────┐    │
│  │  Microphone  │  │ MediaRecorder │  │ AudioContext │  │   React UI   │    │
│  │  + AnalyserN │→ │  (WebM/Opus)  │→ │  (Playback)  │  │ Transcript,  │    │
│  │  (VAD)       │  │  Blob capture │  │  Audio queue │  │ Lead, State  │    │
│  └──────────────┘  └───────────────┘  └─────────────┘  └──────────────┘    │
│                           │ WebSocket (JSON + Base64 audio)                  │
└───────────────────────────┼─────────────────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                        FASTAPI APPLICATION (Python)                           │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                     WebSocket /ws/voice/{session_id}                      │ │
│  │  audio_complete → STT → Agent → LLM stream → TTS → audio_chunk           │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │    REST: POST /chat  POST /chat/stream  POST /voice/turn  GET /leads     │ │
│  │          POST /ingest  GET /health  POST /rag/query                      │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                    AGENT ORCHESTRATION LAYER                              │  │
│  │                                                                           │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────────┐  │  │
│  │  │ State Machine │  │ RAG Retrieval│  │Lead Scoring│  │Prompt Builder│  │  │
│  │  │  9 states     │  │  Top-K match │  │ 6-dimension│  │ + Persona    │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────┘  └──────────────┘  │  │
│  │                                                                           │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │  │
│  │  │Session Memory│  │  Lead Data   │  │  Dual-LLM Router             │  │  │
│  │  │  Per session  │  │  Extraction  │  │  Ollama → early states       │  │  │
│  │  │  In-memory    │  │  JSON parse  │  │  Gemini → reasoning states   │  │  │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────────────────┐  │
│  │ LLM Provider│  │ Vector Store│  │           VOICE LAYER                 │  │
│  │             │  │             │  │                                        │  │
│  │ ┌─────────┐│  │ ┌─────────┐ │  │  ┌─────────┐  ┌─────────────────┐   │  │
│  │ │ OpenAI  ││  │ │  Chroma │ │  │  │   STT   │  │      TTS        │   │  │
│  │ │Anthropic││  │ │  Qdrant │ │  │  │Deepgram │  │ Cartesia        │   │  │
│  │ │ Ollama  ││  │ └─────────┘ │  │  │Whisper  │  │ ElevenLabs      │   │  │
│  │ │ Gemini  ││  │             │  │  └─────────┘  │ OpenAI / Deepgr.│   │  │
│  │ └─────────┘│  │             │  │               └─────────────────┘   │  │
│  └─────────────┘  └─────────────┘  │       ↑ barge-in detection          │  │
│                                     └──────────────────────────────────────┘  │
│                                                                               │
│  ┌───────────────────────┐  ┌──────────────────────────────────────────────┐  │
│  │  knowledge_base.xlsx  │  │  leads.xlsx  (28 fields per qualified lead)  │  │
│  │  (RAG source of truth)│  │  Auto-saved, append-safe, dedup by email    │  │
│  └───────────────────────┘  └──────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 19, TypeScript, Vite 8, Web Audio API, Lucide icons |
| **Backend** | Python 3.11, FastAPI, Uvicorn, Pydantic, asyncio |
| **LLM** | OpenAI GPT-4o, Anthropic Claude, Ollama Llama 3.1, Google Gemini 2.5 Flash |
| **STT** | Deepgram Nova-2 (default), OpenAI Whisper |
| **TTS** | Cartesia Sonic (default), ElevenLabs Turbo v2.5, OpenAI TTS, Deepgram Aura |
| **Vector Store** | ChromaDB (default, zero-infra), Qdrant (production) |
| **RAG** | Pandas (Excel ingestion), cosine similarity over embedded chunks |
| **Lead Storage** | Excel (pandas + openpyxl), 28-field structured output |
| **Communication** | WebSocket (JSON + Base64 audio), REST API |
| **Containerization** | Docker, Docker Compose |

---

## Conversation Workflow

The agent drives a **9-state sales funnel**. Each state has specific behavioral instructions injected into the LLM prompt.

```
  ┌──────────┐
  │ GREETING │  "Welcome to Trango Tech! How can I help?"
  └────┬─────┘
       ▼
  ┌──────────┐
  │ DISCOVERY│  Asks about business, industry, requirements
  └────┬─────┘
       ▼
  ┌──────────────┐
  │QUALIFICATION │  Evaluates need, budget, authority, timeline
  └────┬─────────┘
       ▼
  ┌───────────────┐
  │RECOMMENDATION │  Suggests specific packages from KB (RAG-grounded)
  └────┬──────────┘
       ▼
  ┌───────────────────┐
  │OBJECTION HANDLING  │  Addresses concerns: cost, timeline, trust, NDAs
  └────┬──────────────┘
       ▼
  ┌───────────────────┐
  │PRICING DISCUSSION  │  Quotes from KB, offers discounts, payment terms
  └────┬──────────────┘
       ▼
  ┌─────────┐
  │ CLOSING │  Proposes next steps, asks for commitment
  └────┬────┘
       ▼
  ┌──────────────┐
  │ LEAD CAPTURE │  Collects name, email, phone, company
  └────┬─────────┘
       ▼
  ┌───────────┐
  │ FOLLOW UP │  Confirms next steps, thanks prospect
  └───────────┘
```

State transitions are driven by keyword detection (pricing terms, objections, contact info, closing signals) combined with conversation turn count and qualification score thresholds.

### Dual-LLM Routing Strategy

To optimize cost and latency, the system routes LLM calls based on conversation state:

| States | LLM Provider | Rationale |
|--------|-------------|-----------|
| Greeting, Discovery, Qualification | **Ollama Llama 3.1** (local) | Simple Q&A, zero cost, instant response on Apple Silicon |
| Recommendation, Objection Handling, Pricing, Closing, Lead Capture, Follow-up | **Google Gemini 2.5 Flash** (cloud) | Complex reasoning, nuanced sales logic, structured extraction |

This saves ~80% of cloud LLM costs since most conversation turns occur in early states.

---

## Folder Structure

```
trango_ai_agent/
├── app/
│   ├── agents/
│   │   ├── llm_provider.py          # LLM abstraction: OpenAI, Anthropic, Ollama, Gemini, Dual routing
│   │   ├── sales_agent.py           # Core orchestrator — state machine + RAG + LLM + lead scoring
│   │   └── session_manager.py       # In-memory session store (replace with Redis for multi-instance)
│   ├── api/
│   │   └── routes.py                # FastAPI REST + WebSocket endpoints
│   ├── core/
│   │   ├── config.py                # All settings via pydantic-settings (reads .env)
│   │   ├── logging_config.py        # Structured logging setup
│   │   └── models.py                # AgentState enum, SessionMemory, LeadData, QualificationState
│   ├── leads/
│   │   └── excel_writer.py          # Append-safe Excel lead writer (28 fields, dedup, color-coding)
│   ├── prompts/
│   │   └── templates.py             # System prompts, persona definition, state instructions
│   ├── rag/
│   │   ├── ingestion.py             # Excel → chunks (per-sheet normalizers)
│   │   ├── indexer.py               # CLI indexer with version tracking (skip if unchanged)
│   │   ├── retrieval.py             # RAG query pipeline (embed → search → format)
│   │   └── vector_store.py          # Chroma + Qdrant backends (abstract base)
│   ├── utils/
│   │   └── __init__.py
│   └── voice/
│       ├── voice_layer.py           # STT, TTS, InterruptionHandler abstractions
│       └── pipecat_pipeline.py      # LiveKit/Pipecat transport (optional)
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # Main app component — call controls, transcript, lead panel
│   │   ├── main.tsx                 # React entry point
│   │   ├── index.css                # Global styles (dark theme)
│   │   ├── hooks/
│   │   │   └── useVoiceClient.ts    # WebSocket, audio capture, VAD, playback — core voice hook
│   │   └── components/
│   │       ├── TranscriptFeed.tsx    # Conversation transcript display
│   │       ├── LeadPanel.tsx         # Live lead data sidebar
│   │       ├── AudioVisualizer.tsx   # Real-time audio waveform
│   │       ├── DeviceTester.tsx      # Microphone device test
│   │       └── DiagnosticsDrawer.tsx # Debug info panel
│   ├── package.json
│   ├── vite.config.ts               # Dev server proxy to FastAPI backend
│   ├── index.html
│   └── dist/                        # Production build output
├── static/                          # Copy of frontend build, served by FastAPI in production
├── docker/
│   ├── Dockerfile                   # Multi-stage Python build
│   └── docker-compose.yml           # Agent + Qdrant services
├── kb/
│   └── knowledge_base.xlsx          # Source of truth for all KB content (pricing, packages, FAQs)
├── scripts/
│   ├── generate_knowledge_base.py   # KB generation utility
│   └── ws_demo_client.py            # CLI WebSocket test client
├── tests/
│   └── test_agent.py                # Test suite
├── data/                            # Runtime: chroma_db/, leads.xlsx, kb_version.json
├── logs/                            # Runtime: agent.log
├── main.py                          # Application entry point (uvicorn)
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment template
└── .env                             # Local environment config (not committed)
```

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for frontend development)
- **Ollama** (optional, for local LLM — install from [ollama.com](https://ollama.com))
- At least one API key: Deepgram (STT), Cartesia or ElevenLabs (TTS), and OpenAI or Gemini (LLM)

### 1. Clone and Install Backend

```bash
cd trango_ai_agent

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set your API keys. Minimum required for voice:

```env
# STT (required for voice)
DEEPGRAM_API_KEY=your-deepgram-key

# TTS — choose one:
CARTESIA_API_KEY=your-cartesia-key       # default TTS provider
# or
ELEVENLABS_API_KEY=your-elevenlabs-key   # set TTS_PROVIDER=elevenlabs

# LLM — for Dual routing (default):
GEMINI_API_KEY=your-gemini-key           # high-reasoning states
# Ollama must be running locally for early states
# Or use a single provider:
# LLM_PROVIDER=openai
# OPENAI_API_KEY=your-openai-key
```

### 3. Set Up Ollama (Optional, for Dual-LLM Routing)

```bash
# Install Ollama from https://ollama.com
ollama pull llama3.1:8b
ollama serve   # Runs on localhost:11434
```

### 4. Index the Knowledge Base

```bash
python -m app.rag.indexer
# Output: ✓ Ingestion complete — 72 chunks indexed
```

### 5. Start the Backend

```bash
python main.py
# Server running at http://localhost:8000
```

### 6. Start the Frontend (Development)

```bash
cd frontend
npm install
npm run dev
# Frontend running at http://localhost:5173
```

Open **http://localhost:5173** in your browser, click **Start Call**, and speak.

### 7. Alternative: Use the Production Build

If the `static/` directory contains a built frontend:

```bash
python main.py
# Open http://localhost:8000 — serves both API and frontend
```

### 8. Test via CLI (No Frontend Needed)

```bash
# WebSocket demo client
python scripts/ws_demo_client.py

# REST text chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, I need to build a mobile app for my healthcare startup"}'
```

---

## Frontend Development

The frontend is a React + TypeScript app built with Vite.

### Development Mode

```bash
cd frontend
npm install
npm run dev
```

Vite's dev server runs on port **5173** and proxies all API/WebSocket requests to the FastAPI backend on port **8000** (configured in `vite.config.ts`).

### Build for Production

```bash
cd frontend
npm run build          # Output: frontend/dist/
```

To serve via FastAPI, copy the build to `static/`:

```bash
cp -r frontend/dist/* static/
```

Now `http://localhost:8000` serves both the API and the frontend.

### Key Frontend Components

| File | Purpose |
|------|---------|
| `src/hooks/useVoiceClient.ts` | Core voice hook: WebSocket management, `MediaRecorder` capture, VAD, audio playback queue, barge-in detection |
| `src/App.tsx` | Main layout: call controls, status indicators, timer, transcript/lead panels |
| `src/components/TranscriptFeed.tsx` | Scrolling conversation transcript with user/agent message styling |
| `src/components/LeadPanel.tsx` | Live display of extracted lead data and qualification scores |
| `src/components/AudioVisualizer.tsx` | Real-time microphone audio waveform |
| `src/components/DiagnosticsDrawer.tsx` | Debug panel: WebSocket state, chunks sent/received, logs |
| `src/components/DeviceTester.tsx` | Microphone permission and device testing |

---

## Environment Variables

All settings are managed via `.env` (loaded by pydantic-settings). See `.env.example` for the full template.

### Provider Selection

| Variable | Default | Options |
|----------|---------|---------|
| `LLM_PROVIDER` | `dual` | `openai`, `anthropic`, `ollama`, `gemini`, `dual` |
| `STT_PROVIDER` | `deepgram` | `deepgram`, `openai_whisper`, `assemblyai` |
| `TTS_PROVIDER` | `cartesia` | `cartesia`, `elevenlabs`, `openai_tts`, `deepgram_tts` |
| `VECTOR_STORE` | `chroma` | `chroma`, `qdrant` |
| `DUAL_LLM_ROUTING` | `true` | Enables Ollama (early) + Gemini (reasoning) routing |

### API Keys

| Variable | Required For |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI LLM, Whisper STT, OpenAI TTS, embeddings |
| `GEMINI_API_KEY` | Gemini LLM (dual routing / standalone) |
| `DEEPGRAM_API_KEY` | Deepgram STT and TTS |
| `CARTESIA_API_KEY` | Cartesia TTS |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS |
| `ANTHROPIC_API_KEY` | Anthropic Claude LLM |

### Model Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model name |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Google Gemini model name |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name |
| `DEEPGRAM_STT_MODEL` | `nova-2` | Deepgram STT model |
| `CARTESIA_VOICE_ID` | `a0e99841-...` | Cartesia voice (Sarah — neutral American female) |
| `ELEVENLABS_VOICE_ID` | `EXAVITQu4...` | ElevenLabs voice (Bella — warm professional) |

### Application Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_FILE_PATH` | `./kb/knowledge_base.xlsx` | Knowledge base Excel file |
| `LEADS_FILE_PATH` | `./data/leads.xlsx` | Lead output Excel file |
| `RAG_TOP_K` | `5` | Number of KB chunks retrieved per query |
| `RAG_SCORE_THRESHOLD` | `0.35` | Minimum similarity score for retrieval |
| `SESSION_TIMEOUT_SECONDS` | `1800` | Session TTL (30 min) |
| `MAX_HISTORY_TURNS` | `20` | Conversation history window |
| `BARGE_IN_ENABLED` | `true` | Enable interruption handling |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable hot-reload and debug logging |

---

## API Reference

### `GET /health`
System status, KB document count, active sessions, provider configuration.

### `POST /chat`
Text-based conversation. Creates or resumes a session.

```json
// Request
{ "message": "I need a custom ERP system", "session_id": "optional-uuid" }

// Response
{
  "response": "ERP systems are one of our specialties...",
  "session_id": "abc-123",
  "agent_state": "discovery",
  "lead_temperature": "warm",
  "qualification_score": 0.42,
  "lead_saved": false,
  "sources_used": ["Packages & Plans", "Pricing"]
}
```

### `POST /chat/stream`
Server-Sent Events streaming version of `/chat`. Returns tokens as they are generated.

### `POST /voice/turn`
Single voice turn: upload audio file → transcribe → agent response → TTS audio.

Form fields: `audio` (file upload), `session_id` (query param), `interrupted` (query param).

### `GET /leads`
All captured leads as JSON.

### `GET /leads/download`
Download `leads.xlsx` directly.

### `POST /ingest`
Trigger KB re-indexing.
```json
{ "force": false, "file_path": null }
```

### `GET /ingest/status`
Check if the knowledge base index is up to date.

### `POST /rag/query`
Debug endpoint: test RAG retrieval directly.
```json
{ "query": "mobile app pricing", "top_k": 5 }
```

### `DELETE /session/{session_id}`
End a session and optionally force-save the lead.

---

## WebSocket Protocol

Connect to: `ws://localhost:8000/ws/voice/{session_id}`

### Client → Server Messages

| Type | Payload | Description |
|------|---------|-------------|
| `audio_complete` | `{ type, data: "<base64>", mime: "audio/webm;codecs=opus" }` | **Primary**: complete audio recording (sent when user stops speaking) |
| `text` | `{ type, content: "Hello" }` | Send a text message (bypass STT) |
| `interrupt` | `{ type }` | Explicit barge-in: cancel active TTS and LLM generation |
| `audio` | `{ type, data: "<base64>" }` | Legacy: stream individual audio chunks |
| `end_of_speech` | `{ type }` | Legacy: signal end of speech for chunk-based capture |
| `end_session` | `{ type }` | Close session and force-save lead |

### Server → Client Messages

| Type | Payload | Description |
|------|---------|-------------|
| `transcript` | `{ type, text }` | STT transcription result (user's speech as text) |
| `token` | `{ type, text }` | Streaming LLM token |
| `audio_chunk` | `{ type, data: "<base64>" }` | TTS audio chunk for playback |
| `response_done` | `{ type }` | LLM response complete |
| `state` | `{ type, agent_state, lead_temperature, qualification_score, lead, ... }` | State update after each turn |
| `interrupted` | `{ type }` | Confirms TTS was stopped due to barge-in |
| `lead_saved` | `{ type, lead_id }` | Lead written to Excel |
| `session_ended` | `{ type, lead_id }` | Session closed |
| `error` | `{ type, message }` | Error notification |

---

## Knowledge Base Management

The knowledge base (`kb/knowledge_base.xlsx`) is the single source of truth for all factual information the agent uses. Changes require **zero retraining** — just re-index.

### Update Existing Content

1. Edit `kb/knowledge_base.xlsx` (pricing, packages, FAQs, timelines, etc.)
2. Re-index:
   ```bash
   python -m app.rag.indexer --force
   ```
   Or via API:
   ```bash
   curl -X POST http://localhost:8000/ingest -d '{"force": true}'
   ```

### Add New Sheets

1. Add a new sheet to `knowledge_base.xlsx`
2. Add a normalizer function in `app/rag/ingestion.py`:
   ```python
   def _row_to_text_your_new_sheet(row: pd.Series) -> str:
       return f"Your field: {row.get('FieldName', '')}\n..."
   ```
3. Register it in `_SHEET_NORMALIZERS` and `_SHEET_CATEGORIES`
4. Re-run: `python -m app.rag.indexer --force`

### Version Tracking

The indexer computes a hash of the Excel file. If the hash matches the stored version in `data/kb_version.json`, re-indexing is skipped (unless `--force` is used). This prevents unnecessary re-indexing on server restarts.

---

## Lead Capture

### Automatic Capture

Leads are captured automatically when all conditions are met:
- Lead temperature is **warm** or **hot** (qualification score ≥ 0.35)
- An email address has been collected
- The conversation has had at least 8 turns

A lead is also force-saved on session end (`DELETE /session/{id}` or `end_session` WebSocket message).

### Qualification Scoring

Leads are scored across 6 weighted dimensions:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Budget | 25% | Has the prospect discussed budget or shown willingness to invest? |
| Authority | 25% | Is the prospect a decision-maker? |
| Timeline | 20% | Is there urgency or a defined deadline? |
| Need Clarity | 20% | Has the prospect clearly articulated their requirements? |
| Fit | 10% | Does the project match Trango Tech's service offerings? |

### Output File: `data/leads.xlsx`

28 fields per row including:
- **Contact**: name, email, phone, country, company, industry
- **Project**: service, package, budget, timeline, platform, features, team size
- **Sales**: temperature (cold/warm/hot), qualification score, confidence, status
- **Conversation**: summary, KB sources used, next action, turn count
- **Metadata**: lead ID, session ID, timestamps, source channel

Duplicate prevention uses email + phone + company combination.

---

## Deployment

### Option 1: Local Development (Recommended for Development)

Run the backend and frontend as separate processes:

```bash
# Terminal 1 — Backend
source venv/bin/activate
python main.py                    # http://localhost:8000

# Terminal 2 — Frontend
cd frontend && npm run dev        # http://localhost:5173 (proxies to :8000)

# Terminal 3 — Ollama (if using dual routing)
ollama serve                      # http://localhost:11434
```

### Option 2: Production Build (Single Server)

Build the frontend and serve everything from FastAPI:

```bash
# Build frontend
cd frontend
npm run build

# Copy to static directory
cp -r dist/* ../static/

# Start server
cd ..
python main.py
# Everything served at http://localhost:8000
```

For production, consider using Gunicorn with Uvicorn workers:

```bash
pip install gunicorn
gunicorn app.api.routes:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 120
```

### Option 3: Docker Deployment

```bash
# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Build and start (from project root)
docker compose -f docker/docker-compose.yml up -d

# Check status
docker compose -f docker/docker-compose.yml logs -f agent

# Health check
curl http://localhost:8000/health
```

**Services started:**
- `trango_agent` — FastAPI app on port **8000** (includes built frontend in `/static`)
- `trango_qdrant` — Qdrant vector DB on port **6333** (optional, enable with `VECTOR_STORE=qdrant`)

The Dockerfile uses a multi-stage build:
1. **Builder stage**: installs Python dependencies
2. **Runtime stage**: copies dependencies + app code, runs as non-root user, auto-indexes KB on startup

### Option 4: Cloud Deployment

#### AWS / GCP / Azure VM

```bash
# On the VM:
git clone <your-repo-url>
cd trango_ai_agent

# Install Python 3.11, Node.js 18
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Build frontend
cd frontend && npm install && npm run build && cp -r dist/* ../static/ && cd ..

# Configure
cp .env.example .env && nano .env   # Set API keys

# Index KB and start
python -m app.rag.indexer
# Use systemd or supervisor for process management:
gunicorn app.api.routes:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

#### Reverse Proxy (Nginx)

Place behind Nginx for SSL termination and WebSocket support:

```nginx
server {
    listen 443 ssl;
    server_name sales.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/sales.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sales.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

### Environment Checklist for Production

- [ ] All API keys set in `.env`
- [ ] `DEBUG=false`
- [ ] `LOG_LEVEL=WARNING` or `INFO`
- [ ] `CORS_ORIGINS` restricted to your domain(s)
- [ ] Knowledge base indexed (`python -m app.rag.indexer`)
- [ ] Frontend built and copied to `static/`
- [ ] SSL/TLS configured (required for microphone access in browsers)
- [ ] WebSocket ping/pong enabled (default: 20s interval)
- [ ] Process manager (systemd/supervisor) configured for auto-restart

> **Important**: Browsers require HTTPS to grant microphone access on non-localhost domains. Always deploy behind SSL in production.

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test class
pytest tests/test_agent.py::TestIngestion -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

Tests cover:
- Qualification scoring and state machine transitions
- KB ingestion and chunk normalization
- Prompt template generation per state
- Agent orchestration (mocked LLM + RAG)
- Excel writer (write, read, deduplication)
- API endpoint behavior (mocked dependencies)
- RAG retrieval and formatting

---

## Model Recommendations

### LLM

| Option | Model | Cost | Best For |
|--------|-------|------|----------|
| **Dual Routing (default)** | Ollama Llama 3.1 + Gemini 2.5 Flash | ~$0 + ~$0.15/M | Cost-optimized with strong reasoning where it matters |
| **Production (single provider)** | GPT-4o | ~$5/M tokens | Best overall reasoning and instruction-following |
| **Budget** | GPT-4o-mini | ~$0.15/M tokens | ~95% quality at 1/30th cost |
| **Anthropic** | Claude Sonnet 4 | ~$3/M tokens | Excellent nuanced conversational tone |
| **Self-hosted** | Llama 3.1:70b via Ollama | Free | Requires ~40GB VRAM |

### STT

| Option | Latency | Best For |
|--------|---------|----------|
| **Deepgram Nova-2** (default) | ~300ms | Real-time voice, highest accuracy for business English |
| **OpenAI Whisper** | ~1-2s | Recorded audio, non-real-time, multilingual |

### TTS

| Option | Latency | Best For |
|--------|---------|----------|
| **Cartesia Sonic** (default) | ~200ms | Low-latency streaming, natural voice quality |
| **ElevenLabs Turbo v2.5** | ~500ms | Most human-like warmth, premium quality |
| **OpenAI TTS-1-HD** | ~400ms | Easy setup, competitive pricing |
| **Deepgram Aura** | ~100ms | Ultra-low latency for interrupt-heavy conversations |

### Vector Store

| Option | Best For |
|--------|----------|
| **ChromaDB** (default) | Single-instance, zero setup, persistent on disk |
| **Qdrant** | Multi-instance, high-concurrency production, Docker-ready |

---

## Future Improvements

| Area | Improvement |
|------|-------------|
| **Session Storage** | Replace in-memory with Redis for multi-instance deployments |
| **Phone Integration** | Twilio/Vonage SIP for inbound/outbound voice calls |
| **WhatsApp** | WhatsApp Business API channel handler |
| **CRM Sync** | Push leads to Salesforce / HubSpot / Pipedrive via webhooks |
| **Dashboard** | Analytics dashboard for leads, sessions, conversion rates |
| **A/B Testing** | Test different prompt variants and measure conversion |
| **Multi-language** | Language detection + multilingual STT/TTS (Arabic, Urdu, etc.) |
| **Streaming STT** | Replace batch STT with real-time streaming transcription |
| **Sentiment Analysis** | Detect frustration/excitement in voice and adapt tone |
| **Fine-tuning** | After 500+ conversations, fine-tune for brand tone consistency |
| **Proactive Outreach** | Outbound voice campaigns using the same agent |

---

## License

Proprietary — Trango Tech. All rights reserved.
