##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: test_tools.py
# Purpose: Unit tests for all five tool modules — patient data,
#          payer policy, EHR, EOB OCR, and SOP RAG retrieval.
#          All tests use mock data and run without external APIs.
#
##########################################################

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ------------------------------------------------------------------ #
# Patient data tool tests
# ------------------------------------------------------------------ #

class TestPatientDataTool:
    def test_known_patient_returns_data(self):
        from rcm_denial.tools.patient_data_tool import get_patient_data
        result = get_patient_data("PAT001")
        assert result.patient_id == "PAT001"
        assert result.first_name == "John"
        assert result.last_name == "Smith"
        assert result.is_eligible is True

    def test_known_patient_has_insurance(self):
        from rcm_denial.tools.patient_data_tool import get_patient_data
        result = get_patient_data("PAT001")
        assert len(result.insurance_coverage) == 1
        assert result.insurance_coverage[0].plan_name == "Blue Cross PPO"

    def test_unknown_patient_returns_fallback(self):
        from rcm_denial.tools.patient_data_tool import get_patient_data
        result = get_patient_data("UNKNOWN-999")
        assert result.patient_id == "UNKNOWN-999"
        assert result.is_eligible is False
        assert "not found" in (result.eligibility_notes or "").lower()

    def test_medicare_patient_has_eligibility_notes(self):
        from rcm_denial.tools.patient_data_tool import get_patient_data
        result = get_patient_data("PAT003")
        assert result.eligibility_notes is not None
        assert "Medicare" in result.eligibility_notes


# ------------------------------------------------------------------ #
# Payer policy tool tests
# ------------------------------------------------------------------ #

class TestPayerPolicyTool:
    def test_bcbs_policy_retrieved(self):
        from rcm_denial.tools.payer_policy_tool import get_payer_policy
        result = get_payer_policy("BCBS")
        assert result.payer_id == "BCBS"
        assert result.payer_name == "Blue Cross Blue Shield"
        assert result.timely_filing_limit_days == 365
        assert result.appeal_deadline_days == 180

    def test_bcbs_has_prior_auth_codes(self):
        from rcm_denial.tools.payer_policy_tool import get_payer_policy
        result = get_payer_policy("BCBS")
        assert "27447" in result.prior_auth_required_codes

    def test_medicare_policy_retrieved(self):
        from rcm_denial.tools.payer_policy_tool import get_payer_policy
        result = get_payer_policy("MEDICARE")
        assert result.payer_name == "Centers for Medicare & Medicaid Services"
        assert result.appeal_deadline_days == 120

    def test_unknown_payer_returns_fallback(self):
        from rcm_denial.tools.payer_policy_tool import get_payer_policy
        result = get_payer_policy("UNKNOWN_PAYER")
        assert "manual verification" in result.notes[0].lower()

    def test_case_insensitive_lookup(self):
        from rcm_denial.tools.payer_policy_tool import get_payer_policy
        result_upper = get_payer_policy("BCBS")
        result_lower = get_payer_policy("bcbs")
        assert result_upper.payer_name == result_lower.payer_name


# ------------------------------------------------------------------ #
# EHR tool tests
# ------------------------------------------------------------------ #

class TestEhrTool:
    def test_pat001_has_auth_records(self):
        from rcm_denial.tools.ehr_tool import get_ehr_records
        result = get_ehr_records("PROV-001", "PAT001")
        assert result.has_auth_documentation is True
        assert len(result.prior_auth_records) == 1

    def test_pat002_missing_auth_records(self):
        from rcm_denial.tools.ehr_tool import get_ehr_records
        result = get_ehr_records("PROV-002", "PAT002")
        assert result.has_auth_documentation is False
        assert len(result.prior_auth_records) == 0

    def test_pat001_has_encounter_notes(self):
        from rcm_denial.tools.ehr_tool import get_ehr_records
        result = get_ehr_records("PROV-001", "PAT001")
        assert result.has_encounter_notes is True
        assert "knee" in result.encounter_notes[0].content_summary.lower()

    def test_unknown_patient_returns_empty_ehr(self):
        from rcm_denial.tools.ehr_tool import get_ehr_records
        result = get_ehr_records("PROV-001", "UNKNOWN-PATIENT")
        assert len(result.encounter_notes) == 0
        assert len(result.procedure_details) == 0
        assert result.has_auth_documentation is False


# ------------------------------------------------------------------ #
# EOB OCR tool tests
# ------------------------------------------------------------------ #

class TestEobOcrTool:
    def test_none_path_returns_skipped(self):
        from rcm_denial.tools.eob_ocr_tool import extract_eob_data
        result = extract_eob_data(None)
        assert result.extraction_method == "skipped"

    def test_missing_file_uses_mock_text(self):
        from rcm_denial.tools.eob_ocr_tool import extract_eob_data
        result = extract_eob_data("/nonexistent/path/eob.pdf")
        # Should not raise — uses mock text fallback
        assert result is not None
        assert result.extraction_method in ("pytesseract", "skipped")

    def test_mock_text_extracts_carc_codes(self):
        from rcm_denial.tools.eob_ocr_tool import _get_mock_eob_text, _CARC_PATTERN
        text = _get_mock_eob_text()
        matches = _CARC_PATTERN.findall(text)
        codes = [(m[0] or m[1]).strip().upper() for m in matches if (m[0] or m[1]).strip()]
        assert len(codes) > 0

    def test_mock_text_extracts_rarc_codes(self):
        from rcm_denial.tools.eob_ocr_tool import _get_mock_eob_text, _RARC_PATTERN
        text = _get_mock_eob_text()
        matches = _RARC_PATTERN.findall(text)
        codes = [(m[0] or m[1]).strip().upper() for m in matches if (m[0] or m[1]).strip()]
        assert len(codes) > 0

    def test_amount_extraction(self):
        from rcm_denial.tools.eob_ocr_tool import _extract_amounts
        text = "Total Billed: $15,000.00\nTotal Allowed: $0.00\nTotal Paid: $0.00"
        amounts = _extract_amounts(text)
        assert amounts["billed"] == 15000.00
        assert amounts["allowed"] == 0.00
        assert amounts["paid"] == 0.00

    def test_carc_regex_matches_co_prefix(self):
        import re
        from rcm_denial.tools.eob_ocr_tool import _CARC_PATTERN
        text = "Adjustment: CO-97 applied to claim."
        matches = _CARC_PATTERN.findall(text)
        codes = [(m[0] or m[1]).strip().upper() for m in matches if (m[0] or m[1]).strip()]
        assert "97" in codes

    def test_carc_regex_matches_plain_numeric(self):
        from rcm_denial.tools.eob_ocr_tool import _CARC_PATTERN
        text = "Claim Adjustment Reason Code: 29"
        matches = _CARC_PATTERN.findall(text)
        codes = [(m[0] or m[1]).strip().upper() for m in matches if (m[0] or m[1]).strip()]
        assert "29" in codes


# ------------------------------------------------------------------ #
# SOP RAG tool tests
# ------------------------------------------------------------------ #

class TestSopRagTool:
    def test_carc_29_retrieves_timely_filing_sop(self):
        from rcm_denial.tools.sop_rag_tool import retrieve_sop_guidance
        results = retrieve_sop_guidance(carc_code="29", payer_id="ALL")
        assert len(results) > 0
        titles = [r.title.lower() for r in results]
        assert any("timely" in t for t in titles)

    def test_carc_97_retrieves_auth_sop(self):
        from rcm_denial.tools.sop_rag_tool import retrieve_sop_guidance
        results = retrieve_sop_guidance(carc_code="97", payer_id="BCBS")
        assert len(results) > 0
        # Should match prior auth SOP
        all_carc = [c for r in results for c in r.carc_applicability]
        assert "97" in all_carc

    def test_results_are_sorted_by_relevance(self):
        from rcm_denial.tools.sop_rag_tool import retrieve_sop_guidance
        results = retrieve_sop_guidance(carc_code="50", payer_id="MEDICARE")
        if len(results) > 1:
            assert results[0].relevance_score >= results[1].relevance_score

    def test_top_k_respected(self):
        from rcm_denial.tools.sop_rag_tool import retrieve_sop_guidance
        results = retrieve_sop_guidance(carc_code="97", payer_id="ALL", top_k=2)
        assert len(results) <= 2

    def test_unknown_carc_returns_empty_or_generic(self):
        from rcm_denial.tools.sop_rag_tool import retrieve_sop_guidance
        results = retrieve_sop_guidance(carc_code="999", payer_id="UNKNOWN")
        # Should not raise — returns empty list or generic results
        assert isinstance(results, list)
