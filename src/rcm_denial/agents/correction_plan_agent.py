##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: correction_plan_agent.py
# Purpose: LangGraph node that generates a detailed correction
#          plan for resubmission including code corrections,
#          documentation checklist, and payer-specific instructions.
#
##########################################################

from __future__ import annotations

import time

from tenacity import retry, stop_after_attempt, wait_exponential

from rcm_denial.models.analysis import (
    CodeCorrection,
    CorrectionPlan,
    DocumentationRequirement,
)
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "correction_plan_agent"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def _run_llm_correction(prompt: str, claim_id: str) -> CorrectionPlan:
    try:
        from langchain_openai import ChatOpenAI
        from rcm_denial.config.settings import settings

        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.1,
            max_tokens=settings.openai_max_tokens,
            openai_api_key=settings.openai_api_key,
        )
        structured_llm = llm.with_structured_output(CorrectionPlan)
        return structured_llm.invoke(prompt)
    except ImportError:
        return _rule_based_correction(claim_id)
    except Exception as exc:
        if "rate_limit" in str(exc).lower():
            logger.warning("LLM rate limit — retrying", error=str(exc))
        raise


def _rule_based_correction(claim_id: str) -> CorrectionPlan:
    return CorrectionPlan(
        claim_id=claim_id,
        plan_type="resubmission",
        documentation_required=[
            DocumentationRequirement(
                document_type="encounter_note",
                description="Complete physician encounter note for date of service",
                is_mandatory=True,
            ),
            DocumentationRequirement(
                document_type="medical_necessity_letter",
                description="Physician letter of medical necessity on letterhead",
                is_mandatory=True,
            ),
        ],
        compliance_notes=["Verify all codes comply with current CMS guidelines"],
        resubmission_instructions=[
            "Correct identified coding errors before resubmission",
            "Attach all required documentation",
            "Submit within timely filing window",
        ],
    )


def _build_correction_prompt(state: DenialWorkflowState) -> str:
    claim = state.claim
    analysis = state.denial_analysis
    enriched = state.enriched_data

    payer_guidelines = ""
    if enriched and enriched.payer_policy:
        payer_guidelines = "\n".join(enriched.payer_policy.billing_guidelines[:5])

    ehr_status = "No EHR data available"
    if enriched and enriched.ehr_data:
        ehr = enriched.ehr_data
        ehr_status = (
            f"Encounter notes available: {ehr.has_encounter_notes}, "
            f"Auth records available: {ehr.has_auth_documentation}"
        )

    sop_guidance = ""
    if enriched and enriched.sop_results:
        sop_guidance = enriched.sop_results[0].content_snippet[:400] if enriched.sop_results else ""

    return f"""You are a medical billing correction specialist.
Generate a detailed correction plan for this denied claim to enable resubmission.

CLAIM:
- claim_id: "{claim.claim_id}"
- CPT codes: {claim.cpt_codes}
- Diagnosis codes: {claim.diagnosis_codes}
- Billed amount: ${claim.billed_amount:,.2f}
- CARC: {claim.carc_code}

DENIAL ANALYSIS:
- Root cause: {analysis.root_cause if analysis else 'Unknown'}
- Category: {analysis.denial_category if analysis else 'other'}
- Missing items: {analysis.missing_items if analysis else []}
- Incorrect items: {analysis.incorrect_items if analysis else []}

EHR STATUS: {ehr_status}

PAYER BILLING GUIDELINES:
{payer_guidelines or "Not available"}

RELEVANT SOP:
{sop_guidance or "Not available"}

Generate a CorrectionPlan with:
1. code_corrections — list any CPT/ICD-10 codes that need correction with original, corrected value, type, and reason
2. documentation_required — complete checklist of required documents (mark is_available=true if EHR confirms availability)
3. compliance_notes — any compliance or regulatory considerations
4. resubmission_instructions — step-by-step instructions for the billing team
5. payer_specific_notes — any payer-specific requirements
6. plan_type must be "resubmission"
7. claim_id must be "{claim.claim_id}"
"""


def correction_plan_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Correction Plan Agent.
    Generates detailed resubmission plan with code corrections and documentation checklist.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info("Correction plan agent starting", claim_id=claim.claim_id)

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        prompt = _build_correction_prompt(state)
        plan = _run_llm_correction(prompt, claim.claim_id)
        state.correction_plan = plan

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME, "completed",
            details=(
                f"Code corrections: {len(plan.code_corrections)}, "
                f"Docs required: {len(plan.documentation_required)}, "
                f"Missing docs: {len(plan.missing_documents)}"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Correction plan complete",
            claim_id=claim.claim_id,
            code_corrections=len(plan.code_corrections),
            docs_required=len(plan.documentation_required),
            duration_ms=round(duration_ms, 2),
        )
    except Exception as exc:
        state.add_error(f"Correction plan agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        logger.error("Correction plan agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
