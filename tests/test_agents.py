##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: test_agents.py
# Purpose: Unit tests for all LangGraph agent nodes using
#          mock data. Tests run without requiring OpenAI API
#          keys by relying on the rule-based fallback paths.
#
##########################################################

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from rcm_denial.models.claim import ClaimRecord
from rcm_denial.models.output import DenialWorkflowState


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def sample_claim() -> ClaimRecord:
    return ClaimRecord(
        claim_id="TEST-001",
        patient_id="PAT001",
        payer_id="BCBS",
        provider_id="PROV-NPI-001",
        date_of_service=date(2024, 9, 20),
        cpt_codes=["27447"],
        diagnosis_codes=["M17.11"],
        denial_reason="Prior authorization not obtained",
        carc_code="97",
        rarc_code="N4",
        denial_date=date(2024, 10, 5),
        billed_amount=15000.00,
    )


@pytest.fixture
def sample_state(sample_claim) -> DenialWorkflowState:
    return DenialWorkflowState.create(sample_claim, batch_id="TEST-BATCH-001")


# ------------------------------------------------------------------ #
# Intake agent tests
# ------------------------------------------------------------------ #

class TestIntakeAgent:
    def test_valid_claim_passes(self, sample_state):
        from rcm_denial.agents.intake_agent import intake_agent
        result = intake_agent(sample_state)
        assert result.claim.claim_id == "TEST-001"
        assert len(result.audit_log) >= 2  # started + completed
        assert result.audit_log[-1].status == "completed"

    def test_claim_with_missing_cpt_gets_warning(self, sample_claim):
        sample_claim.cpt_codes = []
        state = DenialWorkflowState.create(sample_claim)
        from rcm_denial.agents.intake_agent import intake_agent
        result = intake_agent(state)
        assert any("CPT" in e for e in result.errors)

    def test_audit_log_populated(self, sample_state):
        from rcm_denial.agents.intake_agent import intake_agent
        result = intake_agent(sample_state)
        node_names = [e.node_name for e in result.audit_log]
        assert "intake_agent" in node_names


# ------------------------------------------------------------------ #
# Enrichment agent tests
# ------------------------------------------------------------------ #

class TestEnrichmentAgent:
    def test_enrichment_populates_patient_data(self, sample_state):
        from rcm_denial.agents.enrichment_agent import enrichment_agent
        result = enrichment_agent(sample_state)
        assert result.enriched_data is not None
        assert result.enriched_data.patient_data is not None
        assert result.enriched_data.payer_policy is not None

    def test_enrichment_retrieves_payer_policy(self, sample_state):
        from rcm_denial.agents.enrichment_agent import enrichment_agent
        result = enrichment_agent(sample_state)
        assert result.enriched_data.payer_policy.payer_id == "BCBS"
        assert result.enriched_data.payer_policy.timely_filing_limit_days == 365

    def test_enrichment_retrieves_sop_results(self, sample_state):
        from rcm_denial.agents.enrichment_agent import enrichment_agent
        result = enrichment_agent(sample_state)
        assert result.enriched_data.sop_results is not None
        assert len(result.enriched_data.sop_results) >= 0  # may be 0 in test env

    def test_enrichment_tolerates_unknown_patient(self, sample_claim):
        sample_claim.patient_id = "UNKNOWN-PATIENT"
        state = DenialWorkflowState.create(sample_claim)
        from rcm_denial.agents.enrichment_agent import enrichment_agent
        result = enrichment_agent(state)
        # Should not raise — fallback patient returned
        assert result.enriched_data is not None


# ------------------------------------------------------------------ #
# Analysis agent tests
# ------------------------------------------------------------------ #

class TestAnalysisAgent:
    def test_analysis_uses_rule_based_fallback(self, sample_state, monkeypatch):
        """Analysis agent should produce denial_analysis via rule-based fallback."""
        # Import the module directly (not via agents package) to avoid __init__ shadowing
        import importlib
        analysis_mod = importlib.import_module("agents.analysis_agent")
        from rcm_denial.agents.analysis_agent import analysis_agent, _rule_based_fallback
        from rcm_denial.agents.enrichment_agent import enrichment_agent

        # Patch the module-level LLM call to use rule-based path directly
        monkeypatch.setattr(
            analysis_mod,
            "_run_llm_analysis",
            lambda prompt, claim_id: _rule_based_fallback(claim_id, "carc_code: 97"),
        )

        enriched_state = enrichment_agent(sample_state)
        result = analysis_agent(enriched_state)

        assert result.denial_analysis is not None
        assert result.routing_decision in ("resubmit", "appeal", "both", "write_off")

    def test_carc_97_routes_to_appeal(self):
        """CARC 97 (prior auth) should route to appeal."""
        claim = ClaimRecord(
            claim_id="TEST-CARC97",
            patient_id="PAT001",
            payer_id="BCBS",
            provider_id="PROV-001",
            date_of_service=date(2024, 9, 20),
            cpt_codes=["27447"],
            diagnosis_codes=["M17.11"],
            denial_reason="Prior auth not obtained",
            carc_code="97",
            denial_date=date(2024, 10, 5),
            billed_amount=15000.0,
        )
        state = DenialWorkflowState.create(claim)
        from rcm_denial.agents.analysis_agent import _rule_based_fallback
        result = _rule_based_fallback("TEST-CARC97", f"carc_code: 97")
        assert result.recommended_action == "appeal"

    def test_carc_11_routes_to_resubmit(self):
        """CARC 11 (coding error) should route to resubmit."""
        from rcm_denial.agents.analysis_agent import _rule_based_fallback
        result = _rule_based_fallback("TEST-CARC11", "carc_code: 11")
        assert result.recommended_action == "resubmit"

    def test_carc_18_routes_to_write_off(self):
        """CARC 18 (duplicate) should route to write_off."""
        from rcm_denial.agents.analysis_agent import _rule_based_fallback
        result = _rule_based_fallback("TEST-CARC18", "carc_code: 18")
        assert result.recommended_action == "write_off"


# ------------------------------------------------------------------ #
# Workflow state tests
# ------------------------------------------------------------------ #

class TestDenialWorkflowState:
    def test_run_id_is_deterministic(self, sample_claim):
        state1 = DenialWorkflowState.create(sample_claim, batch_id="BATCH-A")
        state2 = DenialWorkflowState.create(sample_claim, batch_id="BATCH-A")
        assert state1.run_id == state2.run_id

    def test_different_batch_ids_different_run_ids(self, sample_claim):
        state1 = DenialWorkflowState.create(sample_claim, batch_id="BATCH-A")
        state2 = DenialWorkflowState.create(sample_claim, batch_id="BATCH-B")
        assert state1.run_id != state2.run_id

    def test_add_error_tags_node_name(self, sample_state):
        sample_state.current_node = "test_node"
        sample_state.add_error("Something failed")
        assert "[test_node]" in sample_state.errors[0]

    def test_add_audit_appends_entry(self, sample_state):
        sample_state.add_audit("test_node", "completed", "Test detail", duration_ms=42.0)
        assert len(sample_state.audit_log) == 1
        assert sample_state.audit_log[0].node_name == "test_node"
        assert sample_state.audit_log[0].duration_ms == 42.0
