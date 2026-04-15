##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: evidence_check_agent.py
# Purpose: LangGraph node — LLM CALL 1 of 2.
#
#          Evaluates whether the available evidence (EHR records,
#          EOB data, patient data) is sufficient to support a
#          denial response, and identifies key arguments to make.
#
#          Outputs EvidenceCheckResult which feeds directly into
#          response_agent (LLM call 2).
#
#          Optional 3rd LLM call path:
#            If needs_additional_ehr_fetch=True, a targeted EHR
#            query is triggered before response_agent runs.
#            This applies to "additional evidence required" denials.
#
##########################################################

from __future__ import annotations

import time

from tenacity import retry, stop_after_attempt, wait_exponential

from rcm_denial.models.analysis import EvidenceCheckResult
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "evidence_check_agent"


# ------------------------------------------------------------------ #
# Fallback: rule-based evidence check (when LLM unavailable)
# ------------------------------------------------------------------ #

def _rule_based_evidence_check(state: DenialWorkflowState) -> EvidenceCheckResult:
    """
    Determines evidence sufficiency from structured data without LLM.
    Used when OpenAI is unavailable or for write_off cases.
    """
    claim = state.claim
    analysis = state.denial_analysis
    enriched = state.enriched_data

    action = analysis.recommended_action if analysis else "appeal"
    category = analysis.denial_category if analysis else "other"
    missing = list(analysis.missing_items) if analysis else []
    arguments: list[str] = []
    gaps: list[str] = list(missing)

    has_encounter_notes = bool(enriched and enriched.ehr_data and enriched.ehr_data.has_encounter_notes)
    has_auth = bool(enriched and enriched.ehr_data and enriched.ehr_data.has_auth_documentation)
    has_eob_remarks = bool(enriched and enriched.eob_data and enriched.eob_data.denial_remarks)

    # Build arguments from what IS available
    if has_encounter_notes:
        arguments.append("Physician encounter notes document medical necessity for date of service")
    if has_auth:
        arguments.append("Prior authorization records confirm service was pre-approved")
    if has_eob_remarks:
        arguments.append(f"EOB states: {enriched.eob_data.denial_remarks[0][:100]}")
    if claim.denial_reason:
        arguments.append(f"Addressing denial: {claim.denial_reason[:100]}")

    # Evidence sufficient if we have encounter notes OR auth records (by category)
    sufficient = False
    needs_fetch = False
    fetch_description = ""

    if category == "prior_auth":
        sufficient = has_auth
        if not sufficient:
            gaps.append("Prior authorization approval document not retrieved from EHR")
            needs_fetch = True
            fetch_description = "Prior authorization records for the date of service"
    elif category == "medical_necessity":
        sufficient = has_encounter_notes
        if not sufficient:
            gaps.append("Clinical encounter notes not retrieved from EHR")
            needs_fetch = True
            fetch_description = "Physician encounter notes and diagnostic test results"
    elif category in ("coding_error", "timely_filing"):
        sufficient = True   # rule-based correction, no clinical evidence needed
    elif category == "duplicate_claim":
        sufficient = True   # write-off path, no evidence needed
    else:
        sufficient = has_encounter_notes

    return EvidenceCheckResult(
        claim_id=claim.claim_id,
        evidence_sufficient=sufficient,
        evidence_gaps=gaps,
        key_arguments=arguments,
        needs_additional_ehr_fetch=needs_fetch,
        additional_fetch_description=fetch_description,
        recommended_action_confirmed=action,  # type: ignore[arg-type]
        confidence_score=0.75 if sufficient else 0.55,
        reasoning="Rule-based evidence assessment (LLM unavailable)",
    )


# ------------------------------------------------------------------ #
# LLM-based evidence check
# ------------------------------------------------------------------ #

def _build_evidence_prompt(state: DenialWorkflowState) -> str:
    claim = state.claim
    analysis = state.denial_analysis
    enriched = state.enriched_data

    ehr_summary = "No EHR data retrieved"
    if enriched and enriched.ehr_data:
        ehr = enriched.ehr_data
        parts = []
        if ehr.encounter_notes:
            parts.append(f"Encounter notes ({len(ehr.encounter_notes)}): {ehr.encounter_notes[0].content_summary[:200]}")
        if ehr.prior_auth_records:
            parts.append(f"Auth records ({len(ehr.prior_auth_records)}): {ehr.prior_auth_records[0].content_summary[:150]}")
        if ehr.procedure_details:
            parts.append(f"Procedure records ({len(ehr.procedure_details)}): {ehr.procedure_details[0].content_summary[:150]}")
        ehr_summary = "\n".join(parts) if parts else "EHR retrieved but no documents found"

    eob_summary = "No EOB data"
    if enriched and enriched.eob_data:
        eob = enriched.eob_data
        eob_summary = (
            f"CARC codes: {eob.carc_codes_found}, "
            f"RARC codes: {eob.rarc_codes_found}, "
            f"Denial remarks: {eob.denial_remarks}"
        )

    sop_steps = "No SOP retrieved"
    if enriched and enriched.sop_results:
        sop_steps = enriched.sop_results[0].content_snippet[:400]

    payer_rules = "Not available"
    if enriched and enriched.payer_policy:
        py = enriched.payer_policy
        payer_rules = (
            f"Payer: {py.payer_name} | "
            f"Auth required for: {py.prior_auth_required_codes} | "
            f"Filing limit: {py.timely_filing_limit_days} days"
        )

    reviewer_note = ""
    if getattr(state, "human_notes", ""):
        reviewer_note = (
            f"\nREVIEWER GUIDANCE (from human review cycle {getattr(state, 'review_count', 0)}):\n"
            f"{state.human_notes}\n"
            "Apply this guidance in your evidence assessment.\n"
        )

    return f"""You are a medical billing evidence specialist.
Evaluate whether the available clinical and administrative evidence is sufficient
to support a denial response for this claim.
{reviewer_note}

CLAIM:
- Claim ID: {claim.claim_id}
- CPT codes: {claim.cpt_codes}
- Diagnosis codes: {claim.diagnosis_codes}
- Denial reason: "{claim.denial_reason or 'Not yet extracted from EOB'}"
- CARC code: {claim.carc_code}
- Billed amount: ${claim.billed_amount:,.2f}

DENIAL ANALYSIS (rule-based):
- Category: {analysis.denial_category if analysis else 'unknown'}
- Recommended action: {analysis.recommended_action if analysis else 'appeal'}
- Missing items identified: {analysis.missing_items if analysis else []}
- Root cause: {analysis.root_cause if analysis else 'unknown'}

AVAILABLE EVIDENCE:
EHR Records:
{ehr_summary}

EOB Data:
{eob_summary}

PAYER RULES:
{payer_rules}

RELEVANT SOP PROCEDURE:
{sop_steps}

Evaluate and respond with an EvidenceCheckResult containing:
1. evidence_sufficient: true if available evidence supports the response, false if critical items missing
2. evidence_gaps: list of specific documents or data still needed
3. key_arguments: list of 3-5 strong arguments to include in the denial response
4. needs_additional_ehr_fetch: true ONLY if a specific clinical report (lab/imaging/pathology) is needed but not yet retrieved
5. additional_fetch_description: what exactly to fetch from EHR (if needed)
6. recommended_action_confirmed: confirm or revise the analysis agent's recommendation
7. confidence_score: 0.0-1.0 confidence in your assessment
8. reasoning: brief explanation of your assessment
9. claim_id must be: "{claim.claim_id}"
"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _run_llm_evidence_check(prompt: str, state: DenialWorkflowState) -> EvidenceCheckResult:
    try:
        from langchain_openai import ChatOpenAI
        from rcm_denial.config.settings import settings

        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.1,
            max_tokens=1500,
            openai_api_key=settings.openai_api_key,
        )
        structured_llm = llm.with_structured_output(EvidenceCheckResult)

        # ---- Gap 49: capture token usage --------------------------------
        input_tokens = output_tokens = 0
        try:
            from langchain_community.callbacks import get_openai_callback
            with get_openai_callback() as cb:
                result = structured_llm.invoke(prompt)
            input_tokens  = cb.prompt_tokens
            output_tokens = cb.completion_tokens
        except ImportError:
            result        = structured_llm.invoke(prompt)
            input_tokens  = max(1, len(prompt) // 4)   # rough estimate: ~4 chars/token
            output_tokens = 400                         # typical structured-output size

        try:
            from rcm_denial.services.cost_tracker import record_llm_call
            record_llm_call(
                run_id=state.run_id,
                batch_id=state.batch_id,
                agent_name=NODE_NAME,
                model=settings.openai_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception:
            pass   # cost tracking is non-fatal

        return result

    except ImportError:
        logger.warning("LangChain/OpenAI not available — using rule-based evidence check")
        return _rule_based_evidence_check(state)
    except Exception as exc:
        if "rate_limit" in str(exc).lower() or "429" in str(exc):
            logger.warning("LLM rate limit — retrying", error=str(exc))
        raise


# ------------------------------------------------------------------ #
# LangGraph node
# ------------------------------------------------------------------ #

def evidence_check_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Evidence Check Agent — LLM CALL 1.

    Evaluates available EHR evidence against the denial reason.
    Identifies evidence gaps and key arguments for the response.
    Flags if a targeted EHR fetch is needed (optional 3rd LLM call path).

    Args:
        state: Workflow state with enriched_data and denial_analysis populated.

    Returns:
        Updated state with evidence_check populated and routing_decision
        refined if the LLM revises the recommended action.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info(
        "Evidence check agent starting",
        claim_id=claim.claim_id,
        denial_category=state.denial_analysis.denial_category if state.denial_analysis else "unknown",
        routing_decision=state.routing_decision,
    )

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        prompt = _build_evidence_prompt(state)
        result = _run_llm_evidence_check(prompt, state)

        state.evidence_check = result

        # Let LLM refine the routing decision if it differs from rule-based
        if result.recommended_action_confirmed != state.routing_decision:
            logger.info(
                "Evidence check revised routing decision",
                claim_id=claim.claim_id,
                original=state.routing_decision,
                revised=result.recommended_action_confirmed,
            )
            state.routing_decision = result.recommended_action_confirmed

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Evidence sufficient: {result.evidence_sufficient} | "
                f"Gaps: {len(result.evidence_gaps)} | "
                f"Arguments: {len(result.key_arguments)} | "
                f"Needs EHR fetch: {result.needs_additional_ehr_fetch} | "
                f"Action confirmed: {result.recommended_action_confirmed}"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Evidence check complete",
            claim_id=claim.claim_id,
            evidence_sufficient=result.evidence_sufficient,
            evidence_gaps=len(result.evidence_gaps),
            needs_ehr_fetch=result.needs_additional_ehr_fetch,
            action_confirmed=result.recommended_action_confirmed,
            confidence=result.confidence_score,
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Evidence check agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        logger.error("Evidence check agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
