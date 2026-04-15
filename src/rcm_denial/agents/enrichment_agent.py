##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: enrichment_agent.py
# Purpose: LangGraph node that fans out to all five data
#          sources in parallel (patient, payer, EHR, EOB OCR,
#          SOP RAG) and aggregates results into EnrichedData.
#          Partial failures are tolerated — missing sources are
#          flagged but do not abort the pipeline.
#
##########################################################

from __future__ import annotations

import asyncio
import time
from typing import Any

from rcm_denial.models.claim import (
    EhrData,
    EnrichedData,
    EobExtractedData,
    PatientData,
    PayerPolicy,
    SopResult,
)
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger
from rcm_denial.tools.ehr_tool import get_ehr_records
from rcm_denial.tools.eob_ocr_tool import extract_eob_data
from rcm_denial.tools.patient_data_tool import get_patient_data
from rcm_denial.tools.payer_policy_tool import get_payer_policy
from rcm_denial.tools.sop_rag_tool import retrieve_sop_guidance

logger = get_logger(__name__)

NODE_NAME = "enrichment_agent"


async def _fetch_patient(patient_id: str, claim: Any) -> tuple[str, Any]:
    try:
        return "patient", get_patient_data(patient_id, claim=claim)
    except Exception as exc:
        return "patient_error", str(exc)


async def _fetch_payer(payer_id: str, cpt_codes: list[str], claim: Any) -> tuple[str, Any]:
    try:
        return "payer", get_payer_policy(payer_id, cpt_codes, claim=claim)
    except Exception as exc:
        return "payer_error", str(exc)


async def _fetch_ehr(provider_id: str, patient_id: str, date_of_service: Any, claim: Any) -> tuple[str, Any]:
    try:
        return "ehr", get_ehr_records(provider_id, patient_id, date_of_service, claim=claim)
    except Exception as exc:
        return "ehr_error", str(exc)


async def _fetch_eob(eob_pdf_path: str | None) -> tuple[str, Any]:
    try:
        return "eob", extract_eob_data(eob_pdf_path)
    except Exception as exc:
        return "eob_error", str(exc)


async def _fetch_sop(carc_code: str, rarc_code: str | None, payer_id: str) -> tuple[str, Any]:
    try:
        return "sop", retrieve_sop_guidance(carc_code, rarc_code, payer_id)
    except Exception as exc:
        return "sop_error", str(exc)


async def _enrich_async(state: DenialWorkflowState) -> EnrichedData:
    """Runs all five enrichment tools concurrently via asyncio.gather."""
    claim = state.claim

    results = await asyncio.gather(
        _fetch_patient(claim.patient_id, claim),
        _fetch_payer(claim.payer_id, claim.cpt_codes, claim),
        _fetch_ehr(claim.provider_id, claim.patient_id, claim.date_of_service, claim),
        _fetch_eob(claim.eob_pdf_path),
        _fetch_sop(claim.carc_code, claim.rarc_code, claim.payer_id),
        return_exceptions=False,
    )

    enriched = EnrichedData()
    errors: list[str] = []

    result_map: dict[str, Any] = dict(results)  # type: ignore[arg-type]

    # Patient
    if "patient" in result_map:
        enriched.patient_data = result_map["patient"]
    else:
        errors.append(f"Patient enrichment failed: {result_map.get('patient_error')}")

    # Payer policy
    if "payer" in result_map:
        enriched.payer_policy = result_map["payer"]
    else:
        errors.append(f"Payer policy enrichment failed: {result_map.get('payer_error')}")

    # EHR
    if "ehr" in result_map:
        enriched.ehr_data = result_map["ehr"]
    else:
        errors.append(f"EHR enrichment failed: {result_map.get('ehr_error')}")

    # EOB OCR
    if "eob" in result_map:
        enriched.eob_data = result_map["eob"]
    else:
        errors.append(f"EOB OCR failed: {result_map.get('eob_error')}")

    # SOP RAG
    if "sop" in result_map:
        enriched.sop_results = result_map["sop"] or []
    else:
        errors.append(f"SOP retrieval failed: {result_map.get('sop_error')}")

    enriched.enrichment_errors = errors
    return enriched


def enrichment_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Enrichment Agent.

    Fans out to all five data sources in parallel and populates
    state.enriched_data. Partial failures are tolerated.

    Args:
        state: Current workflow state.

    Returns:
        Updated state with enriched_data populated.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info("Enrichment agent starting", claim_id=claim.claim_id)

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        # Run async enrichment — handle both sync and async call contexts
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _enrich_async(state))
                    enriched = future.result()
            else:
                enriched = loop.run_until_complete(_enrich_async(state))
        except RuntimeError:
            enriched = asyncio.run(_enrich_async(state))

        state.enriched_data = enriched

        # Gap 1 fix: populate denial_reason from EOB OCR output.
        # ClaimRecord.denial_reason is None at intake; EOB extraction
        # is the authoritative source for the human-readable denial text.
        if (
            not state.claim.denial_reason
            and enriched.eob_data
            and enriched.eob_data.denial_remarks
        ):
            state.claim.denial_reason = "; ".join(enriched.eob_data.denial_remarks)
            logger.info(
                "denial_reason populated from EOB OCR",
                claim_id=state.claim.claim_id,
                denial_reason=state.claim.denial_reason[:100],
            )

        # Propagate enrichment errors to main error list
        for err in enriched.enrichment_errors:
            state.add_error(err)

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Enrichment complete. "
                f"Patient: {'OK' if enriched.patient_data else 'MISSING'}, "
                f"Payer: {'OK' if enriched.payer_policy else 'MISSING'}, "
                f"EHR: {'OK' if enriched.ehr_data else 'MISSING'}, "
                f"EOB: {'OK' if enriched.eob_data else 'MISSING'}, "
                f"SOP: {len(enriched.sop_results)} results"
            ),
            duration_ms=duration_ms,
        )

        logger.info(
            "Enrichment agent complete",
            claim_id=claim.claim_id,
            fully_enriched=enriched.is_fully_enriched,
            sop_count=len(enriched.sop_results),
            errors=len(enriched.enrichment_errors),
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Enrichment agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        logger.error("Enrichment agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
