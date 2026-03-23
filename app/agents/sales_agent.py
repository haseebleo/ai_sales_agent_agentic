"""
Trango Tech Sales Agent — Core Orchestrator
Ties together: state machine, RAG retrieval, LLM, lead scoring, and lead capture.
Every customer message flows through process_message().
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Optional

from app.agents.llm_provider import get_llm
from app.core.config import settings
from app.core.models import (
    AgentState,
    LeadData,
    LeadTemperature,
    QualificationState,
    SessionMemory,
)
from app.leads.excel_writer import LeadExcelWriter
from app.prompts.templates import (
    build_conversation_summary_prompt,
    build_lead_extraction_prompt,
    build_qualification_scoring_prompt,
    build_system_prompt,
)
from app.rag.retrieval import retrieve_and_format

logger = logging.getLogger("trango_agent.agent")

# ── Keyword/intent helpers ────────────────────────────────────────────────────

_PRICE_KEYWORDS = re.compile(
    r"\b(price|pricing|cost|how much|budget|rate|charge|fee|quote|package|plan)\b", re.I
)
_PAYMENT_KEYWORDS = re.compile(
    r"\b(payment|pay|instalment|installment|milestone|advance|upfront|escrow|deposit)\b", re.I
)
_REVISION_KEYWORDS = re.compile(r"\b(revision|revisions|changes|change|rework|redo|edit)\b", re.I)
_TIMELINE_KEYWORDS = re.compile(
    r"\b(timeline|delivery|deadline|how long|turnaround|fast|urgent|rush|weeks|months)\b", re.I
)
_DISCOUNT_KEYWORDS = re.compile(r"\b(discount|deal|offer|cheaper|negotiate|reduction|promo)\b", re.I)
_ADDON_KEYWORDS = re.compile(r"\b(add-on|addon|extra|seo|analytics|hosting|maintenance|support)\b", re.I)
_INDUSTRY_KEYWORDS = re.compile(
    r"\b(healthcare|fintech|ecommerce|retail|education|logistics|real estate|finance|hr|crm|erp)\b", re.I
)
_OBJECTION_KEYWORDS = re.compile(
    r"\b(expensive|too much|can't afford|discuss internally|nda|source code|think about|not sure|later|busy)\b", re.I
)
_CONTACT_KEYWORDS = re.compile(
    r"\b(name|email|phone|whatsapp|contact|company|country|reach)\b", re.I
)
_CLOSING_KEYWORDS = re.compile(
    r"\b(book|meeting|call|schedule|proceed|yes|let's go|sign|start|proposal|agree|deal)\b", re.I
)


def _detect_query_category(user_msg: str) -> Optional[str]:
    msg = user_msg.lower()
    if _PRICE_KEYWORDS.search(msg):
        return "pricing"
    if _PAYMENT_KEYWORDS.search(msg):
        return "payment"
    if _REVISION_KEYWORDS.search(msg):
        return "revisions"
    if _TIMELINE_KEYWORDS.search(msg):
        return "delivery"
    if _DISCOUNT_KEYWORDS.search(msg):
        return "discounts"
    if _ADDON_KEYWORDS.search(msg):
        return "addons"
    if _INDUSTRY_KEYWORDS.search(msg):
        return "industry_use_case"
    return None


def _next_state(session: SessionMemory, user_msg: str) -> AgentState:
    """
    Lightweight state transition logic.
    The LLM still drives conversation content; this keeps the state machine consistent.
    """
    msg = user_msg.lower()
    state = session.state

    if state == AgentState.GREETING:
        return AgentState.DISCOVERY

    if state == AgentState.DISCOVERY:
        score = session.qualification.overall_score()
        if score > 0.3 or len(session.history) > 6:
            return AgentState.QUALIFICATION
        return AgentState.DISCOVERY

    if state == AgentState.QUALIFICATION:
        if _OBJECTION_KEYWORDS.search(msg):
            return AgentState.OBJECTION_HANDLING
        if _PRICE_KEYWORDS.search(msg):
            return AgentState.PRICING_DISCUSSION
        score = session.qualification.overall_score()
        if score > 0.5:
            return AgentState.RECOMMENDATION
        return AgentState.QUALIFICATION

    if state == AgentState.RECOMMENDATION:
        if _OBJECTION_KEYWORDS.search(msg):
            return AgentState.OBJECTION_HANDLING
        if _PRICE_KEYWORDS.search(msg) or _PAYMENT_KEYWORDS.search(msg):
            return AgentState.PRICING_DISCUSSION
        if _CLOSING_KEYWORDS.search(msg):
            return AgentState.CLOSING
        return AgentState.RECOMMENDATION

    if state in (AgentState.OBJECTION_HANDLING, AgentState.PRICING_DISCUSSION):
        if _CLOSING_KEYWORDS.search(msg):
            return AgentState.CLOSING
        if _OBJECTION_KEYWORDS.search(msg):
            return AgentState.OBJECTION_HANDLING
        return state

    if state == AgentState.CLOSING:
        temp = session.qualification.temperature()
        if temp in (LeadTemperature.HOT, LeadTemperature.WARM):
            return AgentState.LEAD_CAPTURE
        return AgentState.FOLLOW_UP

    if state == AgentState.LEAD_CAPTURE:
        return AgentState.FOLLOW_UP

    return state


# ── Agent Orchestrator ────────────────────────────────────────────────────────

class SalesAgent:
    def __init__(self) -> None:
        self._llm = get_llm()
        self._lead_writer = LeadExcelWriter()

    async def process_message(
        self,
        session: SessionMemory,
        user_message: str,
        interrupted: bool = False,
    ) -> str:
        """
        Full pipeline: user message → RAG → LLM → state update → response.
        Returns the complete assistant response string.
        """
        # Mark interruption state
        if interrupted:
            session.was_interrupted = True

        # 1. Retrieve relevant knowledge
        loop = asyncio.get_running_loop()
        context_block, sources, strong_match = await loop.run_in_executor(
            None,
            lambda: retrieve_and_format(user_message, top_k=settings.RAG_TOP_K),
        )

        # 2. Update state machine
        new_state = _next_state(session, user_message)
        if new_state != session.state:
            session.transition(new_state)

        # 3. Update qualification scores from message content
        await self._update_qualification(session, user_message)

        # 4. Build system prompt with context
        system_prompt = build_system_prompt(session, context_block=context_block)

        # 5. Build message history
        history = session.recent_history(max_turns=settings.MAX_HISTORY_TURNS)
        history.append({"role": "user", "content": user_message})

        # 6. Generate response
        temperature = 0.75 if session.state in (
            AgentState.GREETING, AgentState.DISCOVERY, AgentState.OBJECTION_HANDLING
        ) else 0.6

        response = await self._llm.chat(
            messages=history,
            system=system_prompt,
            temperature=temperature,
            max_tokens=500,
            state=session.state.value,
        )

        # 7. Update session
        session.add_turn("user", user_message, sources=sources)
        session.add_turn("assistant", response, sources=sources)
        session.was_interrupted = False

        # 8. Post-turn: auto-extract and save lead if warm/hot
        await self._post_turn_processing(session)

        logger.info(
            f"[{session.session_id[:8]}] {session.state.value} | "
            f"score={session.qualification.overall_score():.2f} | "
            f"temp={session.qualification.temperature().value}"
        )

        return response

    async def stream_response(
        self,
        session: SessionMemory,
        user_message: str,
        interrupted: bool = False,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming variant. Yields token strings for real-time TTS integration.
        Interruption token '[[STOP]]' can be injected externally to halt generation.
        """
        if interrupted:
            session.was_interrupted = True

        loop = asyncio.get_running_loop()
        context_block, sources, _ = await loop.run_in_executor(
            None,
            lambda: retrieve_and_format(user_message),
        )

        new_state = _next_state(session, user_message)
        if new_state != session.state:
            session.transition(new_state)

        await self._update_qualification(session, user_message)
        system_prompt = build_system_prompt(session, context_block=context_block)
        history = session.recent_history(max_turns=settings.MAX_HISTORY_TURNS)
        history.append({"role": "user", "content": user_message})

        full_response: list[str] = []
        async for token in self._llm.stream_chat(
            messages=history,
            system=system_prompt,
            temperature=0.7,
            max_tokens=500,
            state=session.state.value,
        ):
            full_response.append(token)
            yield token

        assembled = "".join(full_response)
        session.add_turn("user", user_message, sources=sources)
        session.add_turn("assistant", assembled, sources=sources)
        session.was_interrupted = False

        await self._post_turn_processing(session)

    async def _update_qualification(self, session: SessionMemory, user_message: str) -> None:
        """Lightweight heuristic-based scoring update; full LLM scoring on demand."""
        msg = user_message.lower()
        q = session.qualification

        # Need clarity — they've described something concrete
        if any(w in msg for w in ["app", "website", "platform", "system", "software", "build", "create"]):
            q.need_clarity_score = min(1.0, q.need_clarity_score + 0.2)

        # Budget signal
        if re.search(r"\$[\d,]+|\d+k\b|\d+ thousand|\d+ hundred", msg, re.I):
            q.budget_aligned = min(1.0, q.budget_aligned + 0.35)
        elif re.search(r"\bbudget\b|\bafford\b|\brange\b", msg, re.I):
            q.budget_aligned = min(1.0, q.budget_aligned + 0.15)

        # Timeline urgency
        if re.search(r"\burgen|\bASAP\b|\bsoon\b|\bmonth\b|\bweek\b|\bdeadline\b", msg, re.I):
            q.timeline_urgency = min(1.0, q.timeline_urgency + 0.3)

        # Decision maker
        if re.search(r"\bI (need|want|am|run|own|lead)\b|\bour (company|team|business)\b", msg, re.I):
            q.authority_score = min(1.0, q.authority_score + 0.25)
            session.lead.is_decision_maker = True

        # Seriousness
        if len(user_message) > 80:
            q.seriousness_score = min(1.0, q.seriousness_score + 0.1)
        if any(w in msg for w in ["nda", "proposal", "quote", "contract", "requirement"]):
            q.seriousness_score = min(1.0, q.seriousness_score + 0.2)

        # Service fit
        if re.search(
            r"\bweb\b|\bapp\b|\bmobile\b|\bsoftware\b|\berp\b|\bai\b|\bsaas\b|\bui\b|\bux\b",
            msg, re.I
        ):
            q.service_fit_score = min(1.0, q.service_fit_score + 0.2)

        # Update lead temperature
        session.lead.lead_temperature = q.temperature()
        session.lead.confidence_score = q.overall_score()

        # Extract simple contact fields from message
        self._extract_contact_heuristics(session, user_message)

    def _extract_contact_heuristics(self, session: SessionMemory, msg: str) -> None:
        """Regex-based extraction for email and phone to populate lead mid-conversation."""
        lead = session.lead
        if not lead.email:
            email_match = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", msg)
            if email_match:
                lead.email = email_match.group()
                session.discovery_fields_collected.add("email")

        if not lead.phone:
            phone_match = re.search(r"[\+\d][\d\s\-\(\)]{8,}", msg)
            if phone_match:
                lead.phone = phone_match.group().strip()
                session.discovery_fields_collected.add("phone")

    async def _post_turn_processing(self, session: SessionMemory) -> None:
        """
        After each turn: if lead is warm/hot and we have enough data, trigger extraction + save.
        """
        temp = session.qualification.temperature()
        if (
            temp in (LeadTemperature.HOT, LeadTemperature.WARM)
            and not session.lead_saved
            and len(session.history) >= 8
            and session.lead.email  # minimum: we must have an email
        ):
            await self._extract_and_save_lead(session)

    async def _extract_and_save_lead(self, session: SessionMemory) -> None:
        """
        Run structured extraction over conversation, populate LeadData, write to Excel.
        """
        logger.info(f"[{session.session_id[:8]}] Extracting and saving lead...")
        conversation_text = "\n".join(
            f"{t.role.upper()}: {t.content}" for t in session.history
        )

        # Structured field extraction
        extraction_prompt = build_lead_extraction_prompt(conversation_text)
        try:
            raw = await self._llm.chat(
                messages=[{"role": "user", "content": extraction_prompt}],
                system="You are a data extraction assistant. Return only valid JSON.",
                temperature=0.1,
                max_tokens=800,
                state="lead_capture",  # always use Gemini for structured extraction
            )
            raw = raw.strip().lstrip("```json").rstrip("```").strip()
            extracted: dict = json.loads(raw)
        except Exception as e:
            logger.warning(f"Lead extraction parse error: {e}")
            extracted = {}

        # Qualification scoring
        scoring_prompt = build_qualification_scoring_prompt(conversation_text)
        try:
            score_raw = await self._llm.chat(
                messages=[{"role": "user", "content": scoring_prompt}],
                system="You are a lead scoring assistant. Return only valid JSON.",
                temperature=0.1,
                max_tokens=300,
                state="lead_capture",  # always use Gemini for scoring
            )
            score_raw = score_raw.strip().lstrip("```json").rstrip("```").strip()
            scores: dict = json.loads(score_raw)
            q = session.qualification
            q.need_clarity_score = float(scores.get("need_clarity_score", q.need_clarity_score))
            q.budget_aligned = float(scores.get("budget_aligned", q.budget_aligned))
            q.timeline_urgency = float(scores.get("timeline_urgency", q.timeline_urgency))
            q.authority_score = float(scores.get("authority_score", q.authority_score))
            q.seriousness_score = float(scores.get("seriousness_score", q.seriousness_score))
            q.service_fit_score = float(scores.get("service_fit_score", q.service_fit_score))
        except Exception as e:
            logger.warning(f"Scoring parse error: {e}")
            scores = {}

        # Populate LeadData
        lead = session.lead
        lead.session_id = session.session_id
        lead.lead_temperature = session.qualification.temperature()
        lead.confidence_score = session.qualification.overall_score()
        lead.retrieval_sources = ", ".join(session.retrieval_sources_used[-10:])

        for field, value in extracted.items():
            if value is not None and hasattr(lead, field):
                try:
                    setattr(lead, field, value)
                except Exception:
                    pass

        # Write to Excel
        try:
            self._lead_writer.append_lead(lead)
            session.lead_saved = True
            logger.info(
                f"[{session.session_id[:8]}] Lead saved: {lead.lead_id} "
                f"({lead.lead_temperature.value}, score={lead.confidence_score:.2f})"
            )
        except Exception as e:
            logger.error(f"Lead write error: {e}", exc_info=True)

    async def force_save_lead(self, session: SessionMemory) -> Optional[LeadData]:
        """Explicit save — call this at conversation end regardless of temperature."""
        if session.lead_saved:
            return session.lead
        if len(session.history) < 2:
            return None
        await self._extract_and_save_lead(session)
        return session.lead
