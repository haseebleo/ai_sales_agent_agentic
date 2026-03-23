"""
LLM Provider Abstraction Layer
Swap between OpenAI, Anthropic, Ollama, Gemini, or Dual-routing via SETTINGS.LLM_PROVIDER.

Dual Routing Strategy (M4 MacBook optimized):
----------------------------------------------
EARLY / DISCOVERY STATES  → Ollama Llama 3.1 (local, free, fast for simple Q&A)
    GREETING, DISCOVERY, QUALIFICATION

HIGH-REASONING STATES     → Gemini 1.5 Flash (cloud, low-cost, best reasoning)
    RECOMMENDATION, OBJECTION_HANDLING, PRICING_DISCUSSION, CLOSING, LEAD_CAPTURE, FOLLOW_UP

This hybrid approach:
- Saves ~80% of cloud LLM costs (most turns are in early states)
- Uses the right tool for the right job (Gemini's strength = complex reasoning)
- Ollama runs natively on Apple Silicon M4 — instant response, zero cost
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, Optional

from app.core.config import LLMProvider, settings

logger = logging.getLogger("trango_agent.llm")

# States that should use the high-reasoning Gemini model
_GEMINI_STATES = frozenset([
    "recommendation",
    "objection_handling",
    "pricing_discussion",
    "closing",
    "lead_capture",
    "follow_up",
    "escalation",
])


class LLMBase(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> str:
        """Single-turn completion. Returns full response string."""

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming completion. Yields token strings."""


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAILLM(LLMBase):
    def __init__(self) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._model = settings.OPENAI_MODEL
        logger.info(f"OpenAI LLM ready — model: {self._model}")

    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


# ── Anthropic ─────────────────────────────────────────────────────────────────

class AnthropicLLM(LLMBase):
    def __init__(self) -> None:
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._model = settings.ANTHROPIC_MODEL
        logger.info(f"Anthropic LLM ready — model: {self._model}")

    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant.",
            messages=messages,
            temperature=temperature,
        )
        return resp.content[0].text if resp.content else ""

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant.",
            messages=messages,
            temperature=temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text


# ── Ollama (self-hosted, M4 MacBook) ──────────────────────────────────────────

class OllamaLLM(LLMBase):
    def __init__(self) -> None:
        import httpx
        self._base_url = settings.OLLAMA_BASE_URL
        self._model = settings.OLLAMA_MODEL
        self._client = httpx.AsyncClient(timeout=120.0)
        logger.info(f"Ollama LLM ready — model: {self._model} @ {self._base_url}")

    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        resp = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": msgs,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        import json
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": msgs,
                "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        ) as response:
            async for line in response.aiter_lines():
                if line.strip():
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content


# ── Google Gemini 1.5 Flash ───────────────────────────────────────────────────

class GeminiLLM(LLMBase):
    """
    Google Gemini 1.5 Flash — used for high-reasoning states:
    Recommendation, Objection Handling, Pricing, Closing, Lead Capture.
    
    Advantages over Ollama for these states:
    - Superior complex reasoning and multi-step inference
    - Better calibration for sales persuasion and objection reframing
    - Fast streaming (~100ms to first token)
    - Cost: ~$0.075/1M input tokens (very low for sales turn volumes)
    """

    def __init__(self) -> None:
        import google.generativeai as genai
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self._model_name = settings.GEMINI_MODEL
        self._genai = genai
        logger.info(f"Gemini LLM ready — model: {self._model_name}")

    def _build_model(self, system: str, temperature: float, max_tokens: int):
        """Create a GenerativeModel with system instruction and generation config."""
        return self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system or "You are a helpful assistant.",
            generation_config=self._genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )

    def _messages_to_gemini(self, messages: list[dict[str, str]]) -> list[dict]:
        """Convert OpenAI-style messages to Gemini Content format."""
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        return contents

    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> str:
        import asyncio
        model = self._build_model(system, temperature, max_tokens)
        contents = self._messages_to_gemini(messages)

        def _sync_generate():
            response = model.generate_content(contents)
            return response.text or ""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_generate)

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        import asyncio
        import queue
        import threading

        model = self._build_model(system, temperature, max_tokens)
        contents = self._messages_to_gemini(messages)
        token_queue: queue.Queue = queue.Queue()
        SENTINEL = object()

        def _stream_thread():
            try:
                for chunk in model.generate_content(contents, stream=True):
                    text = chunk.text if chunk.text else ""
                    if text:
                        token_queue.put(text)
            except Exception as exc:
                token_queue.put(exc)
            finally:
                token_queue.put(SENTINEL)

        thread = threading.Thread(target=_stream_thread, daemon=True)
        thread.start()

        loop = asyncio.get_running_loop()
        while True:
            token = await loop.run_in_executor(None, token_queue.get)
            if token is SENTINEL:
                break
            if isinstance(token, Exception):
                raise token
            yield token


# ── Dual-Routing LLM ─────────────────────────────────────────────────────────

class DualRoutingLLM(LLMBase):
    """
    Routes LLM calls based on the current agent conversation state:

    OLLAMA  ← GREETING, DISCOVERY, QUALIFICATION
              (Simple Q&A, structured questions — local model is sufficient)

    GEMINI  ← RECOMMENDATION, OBJECTION_HANDLING, PRICING_DISCUSSION,
              CLOSING, LEAD_CAPTURE, FOLLOW_UP, ESCALATION
              (Complex reasoning, persuasion, reframing — Gemini excels here)

    The `state` kwarg (an AgentState.value string) is passed by the SalesAgent
    orchestrator on each call. Falls back to Ollama when state is unknown.
    """

    def __init__(self) -> None:
        self._ollama = OllamaLLM()
        self._gemini = GeminiLLM()
        logger.info(
            "DualRoutingLLM ready — Ollama (discovery/qualification) + "
            "Gemini (recommendation/closing)"
        )

    def _route(self, state: Optional[str]) -> LLMBase:
        if state and state.lower() in _GEMINI_STATES:
            return self._gemini
        return self._ollama

    async def chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> str:
        provider = self._route(state)
        provider_name = "Gemini" if isinstance(provider, GeminiLLM) else "Ollama"
        logger.debug(f"DualRoutingLLM → {provider_name} (state={state})")
        return await provider.chat(
            messages=messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 600,
        state: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        provider = self._route(state)
        provider_name = "Gemini" if isinstance(provider, GeminiLLM) else "Ollama"
        logger.debug(f"DualRoutingLLM stream → {provider_name} (state={state})")
        async for token in provider.stream_chat(
            messages=messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield token


# ── Factory ───────────────────────────────────────────────────────────────────

_llm_instance: LLMBase | None = None


def get_llm() -> LLMBase:
    global _llm_instance
    if _llm_instance is None:
        provider = settings.LLM_PROVIDER
        if provider == LLMProvider.DUAL or (
            provider == LLMProvider.OLLAMA and settings.DUAL_LLM_ROUTING
        ):
            _llm_instance = DualRoutingLLM()
        elif provider == LLMProvider.GEMINI:
            _llm_instance = GeminiLLM()
        elif provider == LLMProvider.ANTHROPIC:
            _llm_instance = AnthropicLLM()
        elif provider == LLMProvider.OLLAMA:
            _llm_instance = OllamaLLM()
        else:
            _llm_instance = OpenAILLM()
    return _llm_instance
