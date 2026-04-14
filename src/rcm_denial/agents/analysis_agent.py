##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: analysis_agent.py
# Purpose: LangGraph node that uses an LLM with structured
#          output to perform CARC/RARC root cause analysis,
#          determine denial category, and recommend next action.
#          Uses tenacity for LLM retry with exponential backoff.
#
##########################################################

from __future__ import annotations

import json
import time
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential

from rcm_denial.models.analysis import DenialAnalysis
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)

NODE_NAME = "analysis_agent"


# ------------------------------------------------------------------ #
# CARC/RARC reference lookup
# ------------------------------------------------------------------ #

def _load_carc_reference() -> dict:
    ref_path = Path(__file__).parent.parent / "data" / "carc_rarc_reference.json"
    if ref_path.exists():
        with open(ref_path) as f:
            return json.load(f)
    return {}


_CARC_REFERENCE: dict = _load_carc_reference()


def _get_carc_description(code: str) -> str:
    carc_data = _CARC_REFERENCE.get("carc_codes", {})
    return carc_data.get(code, {}).get("description", f"CARC {code} — see payer EOB for details")


def _get_rarc_description(code: str | None) -> str:
    if not code:
        return "No RARC code provided"
    rarc_data = _CARC_REFERENCE.get("rarc_codes", {})
    return rarc_data.get(code, {}).get("description", f"RARC {code} — see payer EOB for details")


# ------------------------------------------------------------------ #
# LLM analysis with retry
# ------------------------------------------------------------------ #

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _run_llm_analysis(prompt: str, claim_id: str) -> DenialAnalysis:
    """
    Calls the LLM with structured output to analyze the denial.
    Retries up to 3 times with exponential backoff on rate limit errors.
    """
    try:
        from langchain_openai import ChatOpenAI
        from rcm_denial.config.settings import settings

        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=settings.openai_temperature,
            max_tokens=settings.openai_max_tokens,
            openai_api_key=settings.openai_api_key,
        )

        structured_llm = llm.with_structured_output(DenialAnalysis)
        result = structured_llm.invoke(prompt)
        return result

    except ImportError:
        logger.warning("LangChain/OpenAI not available — using rule-based fallback")
        return _rule_based_fallback(claim_id, prompt)
    except Exception as exc:
        if "rate_limit" in str(exc).lower() or "429" in str(exc):
            logger.warning("LLM rate limit hit — will retry", error=str(exc))
        raise


def _rule_based_fallback(claim_id: str, context: str) -> DenialAnalysis:
    """
    Rule-based analysis fallback when LLM is unavailable.
    Used in testing / development without API keys.
    """
    # Parse CARC from context for basic routing
    carc = "unknown"
    for line in context.split("\n"):
        if "carc_code:" in line.lower():
            carc = line.split(":")[-1].strip().strip('"').upper()
            break

    carc_action_map = {
        "29": ("timely_filing", "resubmit"),
        "97": ("prior_auth", "appeal"),
        "96": ("prior_auth", "appeal"),
        "167": ("prior_auth", "appeal"),
        "50": ("medical_necessity", "appeal"),
        "55": ("medical_necessity", "appeal"),
        "4": ("coding_error", "resubmit"),
        "11": ("coding_error", "resubmit"),
        "16": ("coding_error", "resubmit"),
        "22": ("coding_error", "resubmit"),
        "18": ("duplicate_claim", "write_off"),
        "27": ("eligibility", "appeal"),
        "1": ("eligibility", "appeal"),
    }

    category, action = carc_action_map.get(carc, ("other", "appeal"))

    return DenialAnalysis(
        claim_id=claim_id,
        root_cause=f"Denial based on CARC {carc} — rule-based analysis (LLM unavailable)",
        carc_interpretation=_get_carc_description(carc),
        rarc_interpretation="See EOB for RARC details",
        missing_items=[],
        incorrect_items=[],
        correction_possible=(action != "write_off"),
        recommended_action=action,  # type: ignore[arg-type]
        confidence_score=0.65,
        reasoning=f"Rule-based routing for CARC {carc}. Manual review recommended.",
        denial_category=category,  # type: ignore[arg-type]
    )


def _build_analysis_prompt(state: DenialWorkflowState) -> str:
    claim = state.claim
    enriched = state.enriched_data

    patient_info = ""
    if enriched and enriched.patient_data:
        p = enriched.patient_data
        cov = p.insurance_coverage[0] if p.insurance_coverage else None
        patient_info = (
            f"Patient: {p.first_name} {p.last_name}, "
            f"Eligible: {p.is_eligible}, "
            f"Plan: {cov.plan_name if cov else 'Unknown'}"
        )

    payer_info = ""
    if enriched and enriched.payer_policy:
        py = enriched.payer_policy
        payer_info = (
            f"Payer: {py.payer_name}, "
            f"Timely filing limit: {py.timely_filing_limit_days} days, "
            f"Prior auth required for: {py.prior_auth_required_codes}"
        )

    ehr_info = ""
    if enriched and enriched.ehr_data:
        ehr = enriched.ehr_data
        ehr_info = (
            f"EHR: Has encounter notes: {ehr.has_encounter_notes}, "
            f"Has prior auth records: {ehr.has_auth_documentation}"
        )

    eob_info = ""
    if enriched and enriched.eob_data:
        eob = enriched.eob_data
        eob_info = (
            f"EOB CARC codes found: {eob.carc_codes_found}, "
            f"RARC codes: {eob.rarc_codes_found}, "
            f"Denial remarks: {eob.denial_remarks[:2]}"
        )

    sop_snippets = ""
    if enriched and enriched.sop_results:
        sop_snippets = "\n".join([
            f"- {s.title}: {s.content_snippet[:200]}"
            for s in enriched.sop_results[:2]
        ])

    carc_desc = _get_carc_description(claim.carc_code)
    rarc_desc = _get_rarc_description(claim.rarc_code)

    return f"""You are an expert medical billing specialist analyzing a denied insurance claim.
Analyze this claim denial and provide a structured analysis.

CLAIM DETAILS:
- claim_id: "{claim.claim_id}"
- payer_id: "{claim.payer_id}"
- date_of_service: "{claim.date_of_service}"
- cpt_codes: {claim.cpt_codes}
- diagnosis_codes: {claim.diagnosis_codes}
- denial_reason: "{claim.denial_reason}"
- carc_code: "{claim.carc_code}" — {carc_desc}
- rarc_code: "{claim.rarc_code or 'None'}" — {rarc_desc}
- denial_date: "{claim.denial_date}"
- billed_amount: ${claim.billed_amount:,.2f}

ENRICHED CONTEXT:
{patient_info}
{payer_info}
{ehr_info}
{eob_info}

RELEVANT SOPs:
{sop_snippets or "None retrieved"}

Based on ALL of the above, provide:
1. The root cause of the denial
2. Your interpretation of the CARC and RARC codes in this specific context
3. A list of missing or incorrect items that caused the denial
4. Whether correction is possible (true/false)
5. Your recommended action: "resubmit" (correctable claim), "appeal" (requires formal appeal),
   "both" (correction + appeal simultaneously), or "write_off" (not recoverable)
6. A confidence score (0.0-1.0) for your recommendation
7. Your full reasoning
8. The denial category (timely_filing, medical_necessity, prior_auth, duplicate_claim,
   coding_error, eligibility, coordination_of_benefits, or other)

The claim_id field in your response must be: "{claim.claim_id}"
"""


def analysis_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Denial Analysis Agent.

    Uses LLM with structured output to analyze the denial reason,
    interpret CARC/RARC codes, and recommend corrective action.

    Args:
        state: Current workflow state with enriched_data populated.

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
        prompt = _build_analysis_prompt(state)
        analysis = _run_llm_analysis(prompt, claim.claim_id)

        state.denial_analysis = analysis
        state.routing_decision = analysis.recommended_action

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Action: {analysis.recommended_action}, "
                f"Category: {analysis.denial_category}, "
                f"Confidence: {analysis.confidence_score:.2f}"
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
        state.routing_decision = "appeal"  # Safe default
        logger.error("Analysis agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
