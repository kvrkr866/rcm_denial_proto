##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: analysis_agent.py
# Purpose: LangGraph node — Denial root cause analysis.
#
#          Determines the denial root cause from TWO sources:
#            1. EOB (source of truth) — major code + description,
#               minor code + description, what's missing, where to find it
#            2. Payer SOP (from RAG) — resolution steps per code combination
#
#          NO static CARC rules table — the EOB and SOP are the
#          "rules engine". Every payer can have different procedures
#          for the same CARC code.
#
#          If no EOB is available → claim is SKIPPED (flagged in
#          audit log with reason).
#
##########################################################

from __future__ import annotations

import time

from rcm_denial.models.analysis import DenialAnalysis
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "analysis_agent"


# ------------------------------------------------------------------ #
# Determine denial category from EOB descriptions
# ------------------------------------------------------------------ #

def _categorize_from_eob(major_code: str, major_desc: str, minor_desc: str, summary: str) -> str:
    """
    Derive denial_category from EOB descriptions.
    This replaces the static CARC rules table — the EOB tells us
    what type of denial it is, in the payer's own words.
    """
    desc_combined = f"{major_desc} {minor_desc} {summary}".lower()

    if any(kw in desc_combined for kw in [
        "timely", "filing limit", "filing deadline",
    ]):
        return "timely_filing"

    if any(kw in desc_combined for kw in [
        "medical necessity", "not medically necessary", "not deemed",
        "level of service", "experimental",
    ]):
        return "medical_necessity"

    if any(kw in desc_combined for kw in [
        "authorization", "precertification", "auth", "pre-approval",
    ]):
        return "prior_auth"

    if any(kw in desc_combined for kw in [
        "duplicate", "previously submitted", "already adjudicated",
    ]):
        return "duplicate_claim"

    if any(kw in desc_combined for kw in [
        "invalid code", "coding error", "incorrect code", "modifier",
        "invalid procedure", "non-specific diagnosis", "provider identifier",
        "npi", "billing error", "lacks information",
    ]):
        return "coding_error"

    if any(kw in desc_combined for kw in [
        "eligibility", "not eligible", "coverage terminated",
        "not insured", "not enrolled",
    ]):
        return "eligibility"

    if any(kw in desc_combined for kw in [
        "coordination", "other payer", "cob", "primary payer",
    ]):
        return "coordination_of_benefits"

    if any(kw in desc_combined for kw in [
        "attachment", "documentation required", "additional documentation",
        "medical record", "operative report", "missing patient",
    ]):
        return "other"  # documentation deficiency — not a clinical denial

    return "other"


def _determine_action_from_eob(category: str, summary: str) -> str:
    """
    Determine recommended action based on denial category and what's missing.
    The SOP provides the detailed steps; this just picks the path.
    """
    s = summary.lower()

    # Missing documentation → resubmit with the missing document
    if any(kw in s for kw in [
        "missing medical record", "missing operative", "missing pathology",
        "missing clinical", "missing documentation",
    ]):
        return "resubmit"

    # Missing auth/reference → resubmit with the auth number
    if any(kw in s for kw in [
        "missing authorization", "missing auth", "missing reference",
    ]):
        return "resubmit"

    # Missing provider info → resubmit with corrected claim
    if any(kw in s for kw in [
        "missing provider", "missing npi", "provider identifier",
    ]):
        return "resubmit"

    # Category-based fallback
    if category == "medical_necessity":
        return "appeal"
    if category == "timely_filing":
        return "appeal"  # need to appeal with proof of timely submission
    if category == "prior_auth":
        return "appeal"  # retro-auth request + appeal
    if category == "duplicate_claim":
        return "resubmit"  # resubmit as replacement
    if category == "coding_error":
        return "resubmit"
    if category == "eligibility":
        return "resubmit"

    return "appeal"  # default


# ------------------------------------------------------------------ #
# Main analysis — driven by EOB + SOP
# ------------------------------------------------------------------ #

def _analyze_from_eob(state: DenialWorkflowState) -> DenialAnalysis:
    """
    Build DenialAnalysis entirely from EOB denial detail + payer SOP.
    No static rules table — the EOB is the source of truth.
    """
    claim = state.claim
    enriched = state.enriched_data
    eob_detail = enriched.eob_data.denial_detail

    # Category from EOB descriptions
    category = _categorize_from_eob(
        eob_detail.major_code,
        eob_detail.major_description,
        eob_detail.minor_description,
        eob_detail.missing_summary,
    )

    # Action from EOB summary
    action = _determine_action_from_eob(category, eob_detail.missing_summary)

    # Refine action based on claim-level signals
    if claim.appealable is False and claim.rebillable is False:
        action = "write_off"
    elif action == "resubmit" and claim.rebillable is False:
        action = "appeal"
    elif action == "appeal" and claim.appealable is False:
        action = "resubmit"

    # Root cause — directly from EOB (payer's own words)
    root_cause = (
        f"Denial {eob_detail.major_code}: {eob_detail.major_description}. "
        f"Specific issue ({eob_detail.minor_code}): {eob_detail.minor_description}. "
        f"Action required: {eob_detail.missing_summary}. "
        f"Artifact to be retrieved from {eob_detail.artifact_source}"
        f"{' or ' + eob_detail.artifact_source_fallback if eob_detail.artifact_source_fallback else ''}."
    )

    # Missing items — from EOB
    missing_items = [
        f"{eob_detail.missing_summary} (source: {eob_detail.artifact_source})"
    ]

    # Enrich with SOP guidance if available
    sop_context = ""
    if enriched and enriched.sop_results:
        sop_context = enriched.sop_results[0].content_snippet[:300]
        # Check if SOP mentions specific steps for this code
        if eob_detail.minor_code.lower() in sop_context.lower():
            root_cause += f" Payer SOP has specific resolution steps for {eob_detail.minor_code}."

    # CARC/RARC descriptions — directly from EOB GLOSSARY
    carc_desc = f"{eob_detail.major_code}: {eob_detail.major_description}"
    rarc_desc = f"{eob_detail.minor_code}: {eob_detail.minor_description}" if eob_detail.minor_code else "N/A"

    reasoning = (
        f"Denial analysis based on EOB from {claim.payer_name or claim.payer_id}. "
        f"{eob_detail.major_code} indicates {eob_detail.major_description.split('.')[0].lower()}. "
        f"{eob_detail.minor_code} specifies: {eob_detail.minor_description.split('.')[0].lower()}. "
        f"Resolution: {action} with {eob_detail.missing_summary.lower()} "
        f"retrieved from {eob_detail.artifact_source}."
    )

    return DenialAnalysis(
        claim_id=claim.claim_id,
        root_cause=root_cause,
        carc_interpretation=carc_desc,
        rarc_interpretation=rarc_desc,
        missing_items=missing_items,
        incorrect_items=[],
        correction_possible=action in ("resubmit", "both"),
        recommended_action=action,          # type: ignore[arg-type]
        confidence_score=0.90,              # high confidence — based on payer's own EOB
        reasoning=reasoning,
        denial_category=category,           # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ #
# LangGraph node
# ------------------------------------------------------------------ #

def analysis_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Denial Analysis.

    Driven by EOB (source of truth) + payer SOP.
    If no EOB available → claim is flagged and skipped.
    """
    start = time.perf_counter()
    claim = state.claim
    enriched = state.enriched_data

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info("Analysis agent starting", claim_id=claim.claim_id)

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        # ── Check EOB availability — source of truth ──────────
        has_eob = (
            enriched
            and enriched.eob_data
            and enriched.eob_data.eob_available
            and enriched.eob_data.denial_detail
        )

        if not has_eob:
            # No EOB → flag and skip this claim
            reason = "EOB not available — cannot determine accurate denial root cause"
            if enriched and enriched.eob_data:
                reason = f"EOB file not found: {enriched.eob_data.raw_text_excerpt[:100]}"

            logger.warning(
                "Claim skipped — no EOB available",
                claim_id=claim.claim_id,
                reason=reason,
            )
            state.add_error(f"SKIPPED: {reason}")
            state.add_audit(
                NODE_NAME, "skipped",
                details=reason,
            )
            # Set minimal analysis so downstream agents know this was skipped
            state.routing_decision = ""
            state.is_complete = True
            return state

        # ── Analyze from EOB + SOP ────────────────────────────
        analysis = _analyze_from_eob(state)

        state.denial_analysis = analysis
        state.routing_decision = analysis.recommended_action

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Action: {analysis.recommended_action} | "
                f"Category: {analysis.denial_category} | "
                f"Confidence: {analysis.confidence_score:.2f} | "
                f"Root cause: {analysis.missing_items[0] if analysis.missing_items else 'N/A'} | "
                f"Data source: EOB"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Analysis complete",
            claim_id=claim.claim_id,
            recommended_action=analysis.recommended_action,
            denial_category=analysis.denial_category,
            confidence=analysis.confidence_score,
            missing_summary=enriched.eob_data.denial_detail.missing_summary,
            data_source="EOB",
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Analysis failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        # Don't guess — flag the error
        state.routing_decision = ""
        logger.error("Analysis agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
