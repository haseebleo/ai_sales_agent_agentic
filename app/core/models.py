"""
Agent state machine, conversation memory, and lead data models.
Tracks every aspect of a live sales conversation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── State Machine ─────────────────────────────────────────────────────────────

class AgentState(str, Enum):
    GREETING = "greeting"
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    RECOMMENDATION = "recommendation"
    OBJECTION_HANDLING = "objection_handling"
    PRICING_DISCUSSION = "pricing_discussion"
    CLOSING = "closing"
    LEAD_CAPTURE = "lead_capture"
    ESCALATION = "escalation"
    FOLLOW_UP = "follow_up"
    ENDED = "ended"


# ── Lead Temperature & Status ─────────────────────────────────────────────────

class LeadTemperature(str, Enum):
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"


class LeadStatus(str, Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    MEETING_REQUESTED = "meeting_requested"
    QUOTE_REQUESTED = "quote_requested"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


# ── Structured Lead Data ──────────────────────────────────────────────────────

class LeadData(BaseModel):
    lead_id: str = Field(default_factory=lambda: f"TT-{uuid.uuid4().hex[:8].upper()}")
    session_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    source_channel: str = "voice"

    # Contact
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None

    # Business context
    industry: Optional[str] = None
    team_size: Optional[str] = None
    is_decision_maker: Optional[bool] = None

    # Project details
    interested_service: Optional[str] = None
    recommended_package: Optional[str] = None
    estimated_budget: Optional[str] = None
    desired_timeline: Optional[str] = None
    project_summary: Optional[str] = None
    required_features: Optional[str] = None
    preferred_platform: Optional[str] = None  # web/mobile/both

    # Sales metadata
    lead_temperature: LeadTemperature = LeadTemperature.COLD
    lead_status: LeadStatus = LeadStatus.NEW
    payment_preference: Optional[str] = None
    confidence_score: float = 0.0
    retrieval_sources: str = ""
    notes: Optional[str] = None
    conversation_summary: Optional[str] = None
    next_action: Optional[str] = None

    def is_duplicate_safe_key(self) -> str:
        return f"{(self.email or '').lower()}|{(self.phone or '').replace(' ', '')}|{(self.company_name or '').lower()}"


# ── Conversation Turn ─────────────────────────────────────────────────────────

class ConversationTurn(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    state: Optional[AgentState] = None
    retrieval_sources: list[str] = Field(default_factory=list)
    interrupted: bool = False


# ── Qualification State ───────────────────────────────────────────────────────

class QualificationState(BaseModel):
    need_clarity_score: float = 0.0       # 0-1: how well we understand their need
    budget_aligned: float = 0.0           # 0-1: budget seems to fit our range
    timeline_urgency: float = 0.0         # 0-1: how soon they want to build
    authority_score: float = 0.0          # 0-1: are they the decision maker
    seriousness_score: float = 0.0        # 0-1: project seems real and scoped
    service_fit_score: float = 0.0        # 0-1: their need fits our services

    def overall_score(
        self,
        budget_w: float = 0.25,
        timeline_w: float = 0.20,
        authority_w: float = 0.25,
        need_w: float = 0.20,
        fit_w: float = 0.10,
    ) -> float:
        return (
            self.need_clarity_score * need_w
            + self.budget_aligned * budget_w
            + self.timeline_urgency * timeline_w
            + self.authority_score * authority_w
            + self.seriousness_score * fit_w
            + self.service_fit_score * fit_w
        ) / (need_w + budget_w + timeline_w + authority_w + fit_w + fit_w)

    def temperature(self) -> LeadTemperature:
        score = self.overall_score()
        if score >= 0.65:
            return LeadTemperature.HOT
        if score >= 0.35:
            return LeadTemperature.WARM
        return LeadTemperature.COLD


# ── Session Memory ────────────────────────────────────────────────────────────

class SessionMemory(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)

    state: AgentState = AgentState.GREETING
    history: list[ConversationTurn] = Field(default_factory=list)
    lead: LeadData = Field(default_factory=LeadData)
    qualification: QualificationState = Field(default_factory=QualificationState)

    # Contextual trackers
    last_recommendation: Optional[str] = None
    objections_handled: list[str] = Field(default_factory=list)
    questions_asked: list[str] = Field(default_factory=list)
    discovery_fields_collected: set[str] = Field(default_factory=set)
    retrieval_sources_used: list[str] = Field(default_factory=list)

    # Interruption state
    was_interrupted: bool = False
    interrupted_response: Optional[str] = None

    # Lead saved
    lead_saved: bool = False

    def add_turn(self, role: str, content: str, sources: list[str] | None = None) -> None:
        self.history.append(
            ConversationTurn(
                role=role,
                content=content,
                state=self.state,
                retrieval_sources=sources or [],
            )
        )
        self.last_active = datetime.utcnow()
        if sources:
            self.retrieval_sources_used.extend(sources)

    def recent_history(self, max_turns: int = 20) -> list[dict[str, str]]:
        """Return last N turns as LLM-ready dicts."""
        turns = self.history[-max_turns:]
        return [{"role": t.role, "content": t.content} for t in turns]

    def context_summary(self) -> str:
        """Compact state dump injected into system prompt."""
        parts = [
            f"Current State: {self.state.value}",
            f"Lead Temperature: {self.qualification.temperature().value}",
            f"Qualification Score: {self.qualification.overall_score():.2f}",
        ]
        if self.lead.full_name:
            parts.append(f"Prospect Name: {self.lead.full_name}")
        if self.lead.company_name:
            parts.append(f"Company: {self.lead.company_name}")
        if self.lead.interested_service:
            parts.append(f"Interested In: {self.lead.interested_service}")
        if self.lead.estimated_budget:
            parts.append(f"Budget Signal: {self.lead.estimated_budget}")
        if self.lead.desired_timeline:
            parts.append(f"Timeline: {self.lead.desired_timeline}")
        if self.last_recommendation:
            parts.append(f"Last Recommendation: {self.last_recommendation}")
        if self.objections_handled:
            parts.append(f"Objections Handled: {', '.join(self.objections_handled)}")
        missing = self._missing_discovery_fields()
        if missing:
            parts.append(f"Still Need To Learn: {', '.join(missing)}")
        return "\n".join(parts)

    def _missing_discovery_fields(self) -> list[str]:
        needed = {
            "service_type": self.lead.interested_service,
            "platform": self.lead.preferred_platform,
            "budget": self.lead.estimated_budget,
            "timeline": self.lead.desired_timeline,
            "industry": self.lead.industry,
            "decision_maker": self.lead.is_decision_maker,
        }
        return [k for k, v in needed.items() if v is None]

    def transition(self, new_state: AgentState) -> None:
        from app.core.logging_config import logger
        logger.info(f"[{self.session_id[:8]}] State: {self.state.value} → {new_state.value}")
        self.state = new_state
