"""
Test Suite — Trango Tech AI Sales Agent
Covers: ingestion, retrieval, state machine, lead extraction, API endpoints.
Run: pytest tests/ -v
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.core.models import (
    AgentState,
    LeadData,
    LeadTemperature,
    QualificationState,
    SessionMemory,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def session():
    s = SessionMemory()
    s.lead.session_id = s.session_id
    return s


@pytest.fixture
def hot_session(session):
    session.qualification.need_clarity_score = 0.9
    session.qualification.budget_aligned = 0.8
    session.qualification.timeline_urgency = 0.9
    session.qualification.authority_score = 0.9
    session.qualification.seriousness_score = 0.8
    session.qualification.service_fit_score = 0.9
    session.lead.email = "ceo@acme.com"
    session.lead.phone = "+1-555-123-4567"
    session.lead.company_name = "Acme Corp"
    session.lead.full_name = "John Smith"
    return session


@pytest.fixture
def sample_lead():
    return LeadData(
        full_name="Sarah Johnson",
        company_name="HealthTech Inc",
        email="sarah@healthtech.io",
        phone="+1-800-555-0199",
        country="United States",
        industry="Healthcare",
        interested_service="Mobile App Development",
        recommended_package="Pro Mobile",
        estimated_budget="$10,000–$15,000",
        desired_timeline="3 months",
        project_summary="Patient appointment booking app with telemedicine features",
        preferred_platform="mobile",
        lead_temperature=LeadTemperature.HOT,
        confidence_score=0.82,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Models & State Machine Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestQualificationState:
    def test_cold_lead_score(self):
        q = QualificationState()
        assert q.overall_score() == 0.0
        assert q.temperature() == LeadTemperature.COLD

    def test_warm_lead_threshold(self):
        q = QualificationState(
            need_clarity_score=0.5,
            budget_aligned=0.4,
            timeline_urgency=0.3,
            authority_score=0.4,
            seriousness_score=0.3,
            service_fit_score=0.4,
        )
        assert q.temperature() == LeadTemperature.WARM

    def test_hot_lead_threshold(self):
        q = QualificationState(
            need_clarity_score=0.9,
            budget_aligned=0.8,
            timeline_urgency=0.9,
            authority_score=0.9,
            seriousness_score=0.8,
            service_fit_score=0.9,
        )
        assert q.temperature() == LeadTemperature.HOT
        assert q.overall_score() >= 0.65

    def test_partial_scores(self):
        q = QualificationState(need_clarity_score=1.0, budget_aligned=1.0)
        score = q.overall_score()
        assert 0.0 < score < 1.0


class TestSessionMemory:
    def test_initial_state(self, session):
        assert session.state == AgentState.GREETING
        assert len(session.history) == 0

    def test_add_turn(self, session):
        session.add_turn("user", "Hello, I need a mobile app")
        session.add_turn("assistant", "Great! Tell me more.")
        assert len(session.history) == 2

    def test_recent_history_limit(self, session):
        for i in range(30):
            session.add_turn("user", f"message {i}")
        recent = session.recent_history(max_turns=10)
        assert len(recent) == 10

    def test_state_transition(self, session):
        session.transition(AgentState.DISCOVERY)
        assert session.state == AgentState.DISCOVERY

    def test_context_summary_with_lead(self, hot_session):
        summary = hot_session.context_summary()
        assert "ceo@acme.com" not in summary  # email shouldn't be in summary
        assert "HOT" in summary.upper() or "hot" in summary.lower()

    def test_missing_discovery_fields(self, session):
        missing = session._missing_discovery_fields()
        assert "service_type" in missing
        assert "budget" in missing

    def test_duplicate_key(self, sample_lead):
        key = sample_lead.is_duplicate_safe_key()
        assert "sarah@healthtech.io" in key
        assert "healthtech inc" in key


class TestLeadData:
    def test_default_lead_id_format(self):
        lead = LeadData()
        assert lead.lead_id.startswith("TT-")
        assert len(lead.lead_id) == 11  # TT- + 8 hex chars

    def test_duplicate_key_empty(self):
        lead = LeadData()
        assert lead.is_duplicate_safe_key() == "||"

    def test_enum_values(self, sample_lead):
        assert sample_lead.lead_temperature.value == "hot"


# ══════════════════════════════════════════════════════════════════════════════
# Ingestion Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestIngestion:
    def test_load_excel_chunks(self):
        from app.rag.ingestion import load_excel_chunks
        chunks = load_excel_chunks("./kb/knowledge_base.xlsx")
        assert len(chunks) > 10
        for chunk in chunks:
            assert "text" in chunk
            assert "metadata" in chunk
            assert len(chunk["text"]) > 20
            assert "sheet_name" in chunk["metadata"]

    def test_chunk_metadata_fields(self):
        from app.rag.ingestion import load_excel_chunks
        chunks = load_excel_chunks("./kb/knowledge_base.xlsx")
        sheets_found = {c["metadata"]["sheet_name"] for c in chunks}
        assert "Services" in sheets_found
        assert "Packages" in sheets_found
        assert "FAQs" in sheets_found

    def test_pricing_chunk_content(self):
        from app.rag.ingestion import load_excel_chunks
        chunks = load_excel_chunks("./kb/knowledge_base.xlsx")
        pricing_chunks = [c for c in chunks if c["metadata"]["sheet_name"] == "Pricing"]
        assert len(pricing_chunks) > 0
        combined = " ".join(c["text"] for c in pricing_chunks)
        assert "$" in combined or "USD" in combined

    def test_kb_version_detection(self, tmp_path):
        import shutil
        from app.rag.ingestion import get_kb_version
        tmp_kb = tmp_path / "test_kb.xlsx"
        shutil.copy("./kb/knowledge_base.xlsx", tmp_kb)
        version = get_kb_version(str(tmp_kb))
        assert len(version) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Prompt Template Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPromptTemplates:
    def test_system_prompt_contains_persona(self, session):
        from app.prompts.templates import build_system_prompt
        prompt = build_system_prompt(session)
        assert "Trango Tech" in prompt
        assert "Alex" in prompt

    def test_system_prompt_with_context(self, session):
        from app.prompts.templates import build_system_prompt
        prompt = build_system_prompt(session, context_block="=== RETRIEVED KNOWLEDGE ===\nTest content")
        assert "RETRIEVED KNOWLEDGE" in prompt

    def test_interruption_note_injected(self, session):
        from app.prompts.templates import build_system_prompt
        session.was_interrupted = True
        prompt = build_system_prompt(session)
        assert "interrupted" in prompt.lower()

    def test_state_specific_instructions(self, session):
        from app.prompts.templates import build_system_prompt
        session.transition(AgentState.RECOMMENDATION)
        prompt = build_system_prompt(session)
        assert "recommend" in prompt.lower()

    def test_lead_extraction_prompt_structure(self):
        from app.prompts.templates import build_lead_extraction_prompt
        prompt = build_lead_extraction_prompt("USER: I need a mobile app. My email is test@test.com")
        assert "full_name" in prompt
        assert "email" in prompt
        assert "JSON" in prompt

    def test_qualification_scoring_prompt(self):
        from app.prompts.templates import build_qualification_scoring_prompt
        prompt = build_qualification_scoring_prompt("We need to build an e-commerce platform.")
        assert "need_clarity_score" in prompt
        assert "budget_aligned" in prompt


# ══════════════════════════════════════════════════════════════════════════════
# Agent Logic Tests (mocked LLM + RAG)
# ══════════════════════════════════════════════════════════════════════════════

class TestSalesAgentLogic:
    @pytest.mark.asyncio
    async def test_process_message_returns_string(self, session):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(
                return_value="Hi! I'm Alex from Trango Tech. What are you looking to build?"
            )
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            from app.agents.sales_agent import SalesAgent
            agent = SalesAgent()
            response = await agent.process_message(session, "Hello")

            assert isinstance(response, str)
            assert len(response) > 0

    @pytest.mark.asyncio
    async def test_state_transitions_to_discovery(self, session):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(return_value="Great to hear! What are you looking to build?")
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            from app.agents.sales_agent import SalesAgent
            agent = SalesAgent()
            await agent.process_message(session, "I need to build a mobile app")

            assert session.state == AgentState.DISCOVERY

    @pytest.mark.asyncio
    async def test_email_extracted_from_message(self, session):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(return_value="Got it. I'll follow up at your email.")
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            from app.agents.sales_agent import SalesAgent
            agent = SalesAgent()
            await agent.process_message(session, "My email is founder@startup.io")

            assert session.lead.email == "founder@startup.io"

    @pytest.mark.asyncio
    async def test_interruption_flag_clears(self, session):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(return_value="Of course, go ahead.")
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            from app.agents.sales_agent import SalesAgent
            agent = SalesAgent()
            await agent.process_message(session, "Wait, I have a question", interrupted=True)

            assert session.was_interrupted is False  # should reset after processing

    @pytest.mark.asyncio
    async def test_qualification_score_increases_with_signals(self, session):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(return_value="That sounds like a great project.")
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            from app.agents.sales_agent import SalesAgent
            agent = SalesAgent()
            initial_score = session.qualification.overall_score()

            await agent.process_message(
                session,
                "I need to build a mobile app for $15,000 ASAP. I'm the CEO and decision maker."
            )

            assert session.qualification.overall_score() > initial_score


# ══════════════════════════════════════════════════════════════════════════════
# Excel Writer Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLeadExcelWriter:
    def test_write_and_read_lead(self, tmp_path, sample_lead):
        from app.leads.excel_writer import LeadExcelWriter
        writer = LeadExcelWriter(
            file_path=str(tmp_path / "test_leads.xlsx"),
            sheet_name="LeadData",
        )
        result = writer.append_lead(sample_lead)
        assert result is True

        leads = writer.get_all_leads()
        assert len(leads) == 1
        assert leads[0]["Email"] == "sarah@healthtech.io"

    def test_duplicate_prevention(self, tmp_path, sample_lead):
        from app.leads.excel_writer import LeadExcelWriter
        writer = LeadExcelWriter(
            file_path=str(tmp_path / "test_leads.xlsx"),
            sheet_name="LeadData",
        )
        writer.append_lead(sample_lead)
        result = writer.append_lead(sample_lead)  # same lead

        assert result is False  # duplicate blocked
        assert len(writer.get_all_leads()) == 1

    def test_multiple_unique_leads(self, tmp_path, sample_lead):
        from app.leads.excel_writer import LeadExcelWriter, LeadData, LeadTemperature
        writer = LeadExcelWriter(
            file_path=str(tmp_path / "test_leads.xlsx"),
            sheet_name="LeadData",
        )
        lead2 = sample_lead.model_copy()
        lead2.email = "other@company.com"
        lead2.phone = "+1-555-999-0000"
        lead2.company_name = "Other Corp"

        writer.append_lead(sample_lead)
        writer.append_lead(lead2)

        assert len(writer.get_all_leads()) == 2

    def test_lead_temperature_enum_serialized(self, tmp_path, sample_lead):
        from app.leads.excel_writer import LeadExcelWriter
        writer = LeadExcelWriter(
            file_path=str(tmp_path / "test_leads.xlsx"),
            sheet_name="LeadData",
        )
        writer.append_lead(sample_lead)
        leads = writer.get_all_leads()
        temp_val = leads[0].get("Lead Temperature")
        assert temp_val in ("hot", "warm", "cold")


# ══════════════════════════════════════════════════════════════════════════════
# API Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIRoutes:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        with (
            patch("app.rag.indexer.run_ingestion"),
            patch("app.rag.vector_store.get_vector_store") as mock_vs,
        ):
            mock_vs.return_value.collection_count.return_value = 50
            from app.api.routes import app
            return TestClient(app)

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"

    def test_chat_endpoint(self, client):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(
                return_value="Hi! I'm Alex from Trango Tech. What are you looking to build today?"
            )
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            resp = client.post("/chat", json={"message": "Hello"})
            assert resp.status_code == 200
            data = resp.json()
            assert "response" in data
            assert "session_id" in data
            assert "agent_state" in data

    def test_chat_session_continuity(self, client):
        with (
            patch("app.agents.sales_agent.get_llm") as mock_llm_factory,
            patch("app.agents.sales_agent.retrieve_and_format") as mock_rag,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(return_value="Tell me more.")
            mock_llm_factory.return_value = mock_llm
            mock_rag.return_value = ("", [], True)

            resp1 = client.post("/chat", json={"message": "Hello"})
            session_id = resp1.json()["session_id"]

            resp2 = client.post("/chat", json={
                "message": "I need a web app",
                "session_id": session_id,
            })
            assert resp2.json()["session_id"] == session_id

    def test_rag_query_endpoint(self, client):
        with patch("app.rag.retrieval.retrieve") as mock_retrieve:
            mock_retrieve.return_value = ([], False)
            resp = client.post("/rag/query", json={"query": "mobile app pricing"})
            assert resp.status_code == 200

    def test_ingest_status_endpoint(self, client):
        with (
            patch("app.rag.ingestion.get_kb_version", return_value="20240101_120000"),
            patch("app.rag.ingestion.load_version_record", return_value={}),
        ):
            resp = client.get("/ingest/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "document_count" in data

    def test_session_not_found(self, client):
        resp = client.get("/session/nonexistent-session-id")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Retrieval Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestRetrieval:
    def test_format_context_block_empty(self):
        from app.rag.retrieval import format_context_block
        result = format_context_block([])
        assert result == ""

    def test_format_context_block_with_chunks(self):
        from app.rag.retrieval import RetrievedChunk, format_context_block
        chunk = RetrievedChunk(
            text="Starter Web Package: $1500",
            metadata={"sheet_name": "Packages", "category": "package_details"},
            score=0.87,
        )
        result = format_context_block([chunk])
        assert "Starter Web Package" in result
        assert "RETRIEVED KNOWLEDGE" in result

    def test_source_labels(self):
        from app.rag.retrieval import RetrievedChunk, source_labels
        chunks = [
            RetrievedChunk("text1", {"category": "pricing"}, 0.9),
            RetrievedChunk("text2", {"category": "payment"}, 0.8),
            RetrievedChunk("text3", {"category": "pricing"}, 0.7),  # duplicate category
        ]
        labels = source_labels(chunks)
        assert len(labels) == 2  # deduplicated
        assert "Pricing" in labels
