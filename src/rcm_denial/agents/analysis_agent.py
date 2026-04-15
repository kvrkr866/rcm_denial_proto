##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: analysis_agent.py
# Purpose: LangGraph node that performs CARC/RARC root cause
#          analysis using a rule-based lookup table.
#          NO LLM is used here — CARC codes are deterministic;
#          routing based on them is faster, free, and consistent.
#
#          LLM calls are reserved for:
#            Call 1 — evidence_check_agent (is evidence sufficient?)
#            Call 2 — response_agent (generate the response)
#
##########################################################

from __future__ import annotations

import json
import time
from pathlib import Path

from rcm_denial.models.analysis import DenialAnalysis
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "analysis_agent"


# ------------------------------------------------------------------ #
# CARC reference (descriptions from carc_rarc_reference.json)
# ------------------------------------------------------------------ #

def _load_carc_reference() -> dict:
    ref_path = Path(__file__).parent.parent / "data" / "carc_rarc_reference.json"
    if ref_path.exists():
        with open(ref_path) as f:
            return json.load(f)
    return {}


_CARC_REFERENCE: dict = _load_carc_reference()


def _get_carc_description(code: str) -> str:
    return (
        _CARC_REFERENCE.get("carc_codes", {})
        .get(code, {})
        .get("description", f"CARC {code} — see payer EOB for details")
    )


def _get_rarc_description(code: str | None) -> str:
    if not code:
        return "No RARC code provided"
    return (
        _CARC_REFERENCE.get("rarc_codes", {})
        .get(code, {})
        .get("description", f"RARC {code} — see payer EOB for details")
    )


# ------------------------------------------------------------------ #
# CARC → denial category + recommended action + missing items lookup
#
# Structure per entry:
#   category          : denial_category literal
#   action            : recommended_action literal
#   correction_possible: bool
#   missing_items     : common items missing for this denial type
#   incorrect_items   : common items incorrect for this denial type
#   confidence        : rule-based confidence (1.0 for well-known codes)
# ------------------------------------------------------------------ #

_CARC_RULES: dict[str, dict] = {
    # ---- Timely filing ----
    "29": {
        "category": "timely_filing",
        "action": "resubmit",
        "correction_possible": True,
        "missing_items": ["Proof of timely filing (clearinghouse report or certified mail receipt)"],
        "incorrect_items": [],
        "confidence": 0.95,
        "reasoning": "Claim submitted past payer's timely filing window. Resubmit with proof of original timely submission if available.",
    },
    # ---- Prior authorization ----
    "97": {
        "category": "prior_auth",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Prior authorization number", "Auth approval letter"],
        "incorrect_items": [],
        "confidence": 0.95,
        "reasoning": "Service requires prior authorization. Verify auth exists; attach auth number or request retro-auth.",
    },
    "96": {
        "category": "prior_auth",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Prior authorization documentation"],
        "incorrect_items": [],
        "confidence": 0.90,
        "reasoning": "Non-covered charge or missing auth. Review plan exclusions and auth requirements.",
    },
    "167": {
        "category": "prior_auth",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Prior authorization for out-of-network provider"],
        "incorrect_items": [],
        "confidence": 0.90,
        "reasoning": "Out-of-network service. Verify credentialing and check gap exception eligibility.",
    },
    "252": {
        "category": "prior_auth",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Authorization number", "Retro-authorization request"],
        "incorrect_items": [],
        "confidence": 0.95,
        "reasoning": "Service performed without required authorization. Request retro-auth and appeal.",
    },
    # ---- Medical necessity ----
    "50": {
        "category": "medical_necessity",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Physician letter of medical necessity", "Clinical notes supporting diagnosis"],
        "incorrect_items": [],
        "confidence": 0.90,
        "reasoning": "Payer determined service not medically necessary. Appeal with clinical documentation.",
    },
    "55": {
        "category": "medical_necessity",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Medical necessity documentation", "Diagnostic test results"],
        "incorrect_items": [],
        "confidence": 0.90,
        "reasoning": "Procedure not medically necessary per payer policy. Appeal with supporting clinical evidence.",
    },
    "170": {
        "category": "medical_necessity",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Medical necessity documentation"],
        "incorrect_items": [],
        "confidence": 0.85,
        "reasoning": "Payment adjusted for medical necessity reasons. Appeal with clinical justification.",
    },
    # ---- Coding errors ----
    "4": {
        "category": "coding_error",
        "action": "resubmit",
        "correction_possible": True,
        "missing_items": [],
        "incorrect_items": ["Service dates on claim", "Procedure or diagnosis code mismatch"],
        "confidence": 0.95,
        "reasoning": "Service date or code inconsistency. Correct and resubmit — do not appeal coding errors.",
    },
    "11": {
        "category": "coding_error",
        "action": "resubmit",
        "correction_possible": True,
        "missing_items": [],
        "incorrect_items": ["Diagnosis code specificity", "ICD-10 code selection"],
        "confidence": 0.95,
        "reasoning": "Diagnosis inconsistent with procedure. Verify ICD-10 specificity and resubmit.",
    },
    "16": {
        "category": "coding_error",
        "action": "resubmit",
        "correction_possible": True,
        "missing_items": ["Missing or incomplete claim field (see RARC for specific field)"],
        "incorrect_items": [],
        "confidence": 0.95,
        "reasoning": "Claim missing required information. Identify missing field from RARC code and resubmit.",
    },
    "22": {
        "category": "coding_error",
        "action": "resubmit",
        "correction_possible": True,
        "missing_items": [],
        "incorrect_items": ["Coordination of benefits — primary payer EOB required"],
        "confidence": 0.90,
        "reasoning": "COB issue. Attach primary payer EOB and resubmit to secondary.",
    },
    "B7": {
        "category": "coding_error",
        "action": "resubmit",
        "correction_possible": True,
        "missing_items": [],
        "incorrect_items": ["Provider not credentialed for this procedure or place of service"],
        "confidence": 0.90,
        "reasoning": "Provider credentialing issue. Verify provider enrollment and resubmit.",
    },
    # ---- Duplicate claim ----
    "18": {
        "category": "duplicate_claim",
        "action": "write_off",
        "correction_possible": False,
        "missing_items": [],
        "incorrect_items": ["Duplicate submission — original claim already adjudicated"],
        "confidence": 0.95,
        "reasoning": "Duplicate of previously adjudicated claim. Verify original claim status; write off if already paid.",
    },
    # ---- Eligibility ----
    "27": {
        "category": "eligibility",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Eligibility verification at date of service"],
        "incorrect_items": [],
        "confidence": 0.90,
        "reasoning": "Insurance not in effect on date of service. Verify coverage dates and appeal if eligible.",
    },
    "1": {
        "category": "eligibility",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Deductible/copay documentation", "Patient eligibility verification"],
        "incorrect_items": [],
        "confidence": 0.85,
        "reasoning": "Patient deductible or co-insurance applies. Verify patient responsibility and appeal if incorrect.",
    },
    # ---- Coordination of benefits ----
    "119": {
        "category": "coordination_of_benefits",
        "action": "appeal",
        "correction_possible": True,
        "missing_items": ["Benefit maximum documentation", "Accumulator verification"],
        "incorrect_items": [],
        "confidence": 0.85,
        "reasoning": "Benefit maximum reached. Verify accumulator and document medical necessity for exception.",
    },
}

_DEFAULT_RULE = {
    "category": "other",
    "action": "appeal",
    "correction_possible": True,
    "missing_items": ["Supporting documentation per payer requirements"],
    "incorrect_items": [],
    "confidence": 0.70,
    "reasoning": "CARC code not in standard lookup — defaulting to appeal. Manual review recommended.",
}


# ------------------------------------------------------------------ #
# Rule-based analysis
# ------------------------------------------------------------------ #

def _rule_based_analysis(state: DenialWorkflowState) -> DenialAnalysis:
    """
    Derives DenialAnalysis deterministically from CARC/RARC codes.

    No LLM call — fast, free, and consistent across identical denial codes.
    For unknown/complex CARC codes the evidence_check_agent (LLM call 1)
    will add clinical context on top of this base analysis.
    """
    claim = state.claim
    carc = str(claim.carc_code).strip().upper()

    rule = _CARC_RULES.get(carc, _DEFAULT_RULE)

    carc_desc = _get_carc_description(carc)
    rarc_desc = _get_rarc_description(claim.rarc_code)

    # Refine action based on claim-level signals from intake agent
    action = rule["action"]
    if claim.appealable is False and claim.rebillable is False:
        action = "write_off"
    elif rule["action"] == "resubmit" and claim.rebillable is False:
        action = "appeal"
    elif rule["action"] == "appeal" and claim.appealable is False:
        action = "resubmit"

    return DenialAnalysis(
        claim_id=claim.claim_id,
        root_cause=f"{rule['reasoning']}",
        carc_interpretation=carc_desc,
        rarc_interpretation=rarc_desc,
        missing_items=rule["missing_items"],
        incorrect_items=rule["incorrect_items"],
        correction_possible=rule["correction_possible"] and action != "write_off",
        recommended_action=action,          # type: ignore[arg-type]
        confidence_score=rule["confidence"],
        reasoning=rule["reasoning"],
        denial_category=rule["category"],   # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ #
# LangGraph node
# ------------------------------------------------------------------ #

def analysis_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Denial Analysis Agent.

    Pure rule-based — no LLM. Maps CARC code to:
      - denial_category (timely_filing, prior_auth, coding_error, etc.)
      - recommended_action (resubmit, appeal, both, write_off)
      - missing_items and incorrect_items checklists
      - confidence_score

    LLM reasoning is added in the next stage (evidence_check_agent).

    Args:
        state: Workflow state with enriched_data populated.

    Returns:
        Updated state with denial_analysis and routing_decision set.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info("Analysis agent starting", claim_id=claim.claim_id, carc=claim.carc_code)

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        analysis = _rule_based_analysis(state)

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
                f"Missing items: {len(analysis.missing_items)}"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Analysis agent complete",
            claim_id=claim.claim_id,
            recommended_action=analysis.recommended_action,
            denial_category=analysis.denial_category,
            confidence=analysis.confidence_score,
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Analysis agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        state.routing_decision = "appeal"
        logger.error("Analysis agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
