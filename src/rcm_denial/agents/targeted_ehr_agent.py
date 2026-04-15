##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: targeted_ehr_agent.py
# Purpose: Gaps 9/11 — Stage 2 targeted EHR fetch.
#
#          Optional LangGraph node — triggered only when
#          evidence_check_agent sets needs_additional_ehr_fetch=True.
#
#          Fetches specific diagnostic reports (lab, imaging, pathology)
#          that were identified as missing in the evidence assessment.
#          Appends DiagnosticReport objects to state.enriched_data.ehr_data.
#
#          This is LLM call 3 path — no LLM is used in THIS agent.
#          The fetched reports become richer context for response_agent
#          (which may be considered the "3rd call" in complex cases).
#
#          Graph edge (conditional):
#            evidence_check_agent
#              ├─ needs_fetch=False → response_agent  (default path)
#              └─ needs_fetch=True  → targeted_ehr_agent → response_agent
#
##########################################################

from __future__ import annotations

import time

from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "targeted_ehr_agent"


# ------------------------------------------------------------------ #
# Stage 2 fetch dispatcher
# ------------------------------------------------------------------ #

def _fetch_diagnostic_reports(state: DenialWorkflowState) -> list:
    """
    Dispatches the Stage 2 diagnostic report fetch via the configured
    EHR adapter (batch session if available, else fresh mock adapter).

    Returns a list of DiagnosticReport objects.
    """
    from rcm_denial.config.settings import settings
    from rcm_denial.services.data_source_adapters import (
        get_ehr_session,
        get_emr_adapter,
    )

    claim = state.claim
    fetch_desc = ""
    if state.evidence_check:
        fetch_desc = state.evidence_check.additional_fetch_description

    # Prefer batch-level session adapter; fall back to fresh instance
    adapter = get_ehr_session(state.batch_id) or get_emr_adapter(settings.emr_adapter)

    logger.info(
        "Stage 2 EHR fetch",
        claim_id=claim.claim_id,
        fetch_description=fetch_desc,
        adapter_type=type(adapter).__name__,
    )

    return adapter.get_diagnostic_reports(
        patient_id=claim.patient_id,
        provider_id=claim.provider_id,
        claim=claim,
        fetch_description=fetch_desc,
    )


# ------------------------------------------------------------------ #
# Optional: OCR any PDF paths returned in evidence_check fetch hints
# ------------------------------------------------------------------ #

def _ocr_report_pdfs(state: DenialWorkflowState) -> list:
    """
    If the fetch_description contains file paths to clinical PDFs
    (e.g. from an RPA adapter that downloaded files), OCR them.

    Returns additional DiagnosticReport objects from OCR.
    """
    from pathlib import Path
    from rcm_denial.tools.clinical_ocr_tool import extract_clinical_reports_from_paths

    fetch_desc = ""
    if state.evidence_check:
        fetch_desc = state.evidence_check.additional_fetch_description

    # Extract any .pdf file paths mentioned in the fetch description
    import re
    pdf_paths = re.findall(r"[\w/\\:.~-]+\.pdf", fetch_desc, re.IGNORECASE)
    if not pdf_paths:
        return []

    logger.info(
        "OCR-ing clinical PDFs from fetch description",
        claim_id=state.claim.claim_id,
        pdf_count=len(pdf_paths),
    )
    return extract_clinical_reports_from_paths(
        pdf_paths,
        claim_id=state.claim.claim_id,
        fetch_description=fetch_desc,
    )


# ------------------------------------------------------------------ #
# Update EHR data in state with fetched reports
# ------------------------------------------------------------------ #

def _merge_reports(state: DenialWorkflowState, reports: list) -> None:
    """Appends fetched DiagnosticReports to state.enriched_data.ehr_data."""
    if not reports:
        return

    if state.enriched_data is None:
        logger.warning("enriched_data is None in targeted_ehr_agent — skipping merge")
        return

    if state.enriched_data.ehr_data is None:
        # Minimal EhrData shell to hold the reports
        from rcm_denial.models.claim import EhrData
        state.enriched_data.ehr_data = EhrData(
            patient_id=state.claim.patient_id,
            provider_id=state.claim.provider_id,
        )

    state.enriched_data.ehr_data.diagnostic_reports.extend(reports)
    state.enriched_data.ehr_data.stage2_fetched = True


# ------------------------------------------------------------------ #
# LangGraph node
# ------------------------------------------------------------------ #

def targeted_ehr_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Targeted EHR Agent — Stage 2 fetch (conditional).

    Triggered only when evidence_check_agent.needs_additional_ehr_fetch=True.
    Fetches diagnostic reports (lab, imaging, pathology) that were flagged
    as missing in the evidence assessment.

    After this node completes, response_agent runs with the enriched
    diagnostic context, enabling it to cite specific clinical findings.

    Args:
        state: Workflow state with evidence_check populated.

    Returns:
        Updated state with diagnostic_reports appended to ehr_data.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info(
        "Targeted EHR agent starting",
        claim_id=claim.claim_id,
        fetch_description=(
            state.evidence_check.additional_fetch_description
            if state.evidence_check else "unknown"
        ),
    )

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        # ---- Stage 2: fetch diagnostic reports via adapter ----
        reports = _fetch_diagnostic_reports(state)

        # ---- Also OCR any PDF paths in the fetch description ----
        ocr_reports = _ocr_report_pdfs(state)
        all_reports = reports + ocr_reports

        # ---- Merge into EHR data ----
        _merge_reports(state, all_reports)

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Fetched: {len(reports)} reports | "
                f"OCR: {len(ocr_reports)} PDFs | "
                f"Total: {len(all_reports)} diagnostic reports added"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Targeted EHR agent complete",
            claim_id=claim.claim_id,
            reports_fetched=len(reports),
            ocr_reports=len(ocr_reports),
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Targeted EHR agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        logger.error("Targeted EHR agent failed", claim_id=claim.claim_id, error=str(exc))
        # Non-fatal: pipeline continues to response_agent without the extra reports

    return state
