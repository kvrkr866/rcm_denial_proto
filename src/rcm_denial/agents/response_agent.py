##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: response_agent.py
# Purpose: LangGraph node — LLM CALL 2 of 2.
#
#          Prepares the complete denial response using the evidence
#          assessment from evidence_check_agent (LLM call 1).
#          Handles all routing outcomes in a single LLM call:
#
#            "resubmit" → corrected claim instructions + cover letter
#            "appeal"   → formal appeal letter + supporting doc list
#            "both"     → correction plan + appeal letter
#            "write_off"→ write-off documentation (no LLM needed)
#
#          Populates state.correction_plan and/or state.appeal_package
#          (same models as before — document_packaging_agent unchanged).
#
##########################################################

from __future__ import annotations

import time
from datetime import date
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from rcm_denial.models.analysis import (
    CodeCorrection,
    CorrectionPlan,
    DocumentationRequirement,
)
from rcm_denial.models.appeal import AppealLetter, AppealPackage, SupportingDocument
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "response_agent"


# ------------------------------------------------------------------ #
# Prompt builder — single rich context for both resubmit + appeal
# ------------------------------------------------------------------ #

def _build_response_prompt(state: DenialWorkflowState) -> str:
    claim = state.claim
    analysis = state.denial_analysis
    evidence = state.evidence_check
    enriched = state.enriched_data
    action = state.routing_decision

    payer_name = claim.payer_name or claim.payer_id
    portal_url = None
    appeal_deadline_days = 180
    billing_guidelines: list[str] = []

    if enriched and enriched.payer_policy:
        py = enriched.payer_policy
        payer_name = py.payer_name or payer_name
        portal_url = py.appeal_portal_url
        appeal_deadline_days = py.appeal_deadline_days
        billing_guidelines = py.billing_guidelines[:4]

    patient_name = claim.patient_name or claim.patient_id
    if enriched and enriched.patient_data:
        p = enriched.patient_data
        patient_name = f"{p.first_name} {p.last_name}"

    sop_steps = "Follow standard denial response procedures"
    if enriched and enriched.sop_results:
        sop_steps = enriched.sop_results[0].content_snippet[:500]

    key_arguments = evidence.key_arguments if evidence else []
    evidence_gaps = evidence.evidence_gaps if evidence else []

    instructions = {
        "resubmit": (
            "Generate a CorrectionPlan with: "
            "(1) code_corrections for any incorrect CPT/ICD-10 codes, "
            "(2) documentation_required checklist, "
            "(3) resubmission_instructions step-by-step, "
            "(4) payer_specific_notes. "
            "Also generate an AppealLetter as a brief cover letter explaining the correction."
        ),
        "appeal": (
            "Generate an AppealLetter with: subject_line, opening_paragraph, "
            "denial_summary, clinical_justification using key_arguments, "
            "regulatory_basis citing CMS guidelines or plan terms, "
            "closing_paragraph requesting expedited review, signature_block. "
            "Also generate a CorrectionPlan documenting required supporting documents."
        ),
        "both": (
            "Generate both a CorrectionPlan (corrected claim instructions) AND "
            "an AppealLetter (formal appeal). The appeal letter should reference "
            "that a corrected claim is being submitted simultaneously."
        ),
    }.get(action, "Generate an AppealLetter as the default response.")

    reviewer_note = ""
    if getattr(state, "human_notes", ""):
        reviewer_note = (
            f"\nREVIEWER GUIDANCE (review cycle {getattr(state, 'review_count', 0)}):\n"
            f"{state.human_notes}\n"
            "Incorporate this guidance into the response — it overrides general defaults.\n"
        )

    return f"""You are a medical billing specialist preparing a denial response package.
{reviewer_note}

CLAIM:
- Claim ID: {claim.claim_id}
- Patient: {patient_name}
- Payer: {payer_name}
- Date of Service: {claim.date_of_service}
- CPT codes: {claim.cpt_codes}
- Diagnosis codes: {claim.diagnosis_codes}
- Billed amount: ${claim.billed_amount:,.2f}
- Denial reason: "{claim.denial_reason or 'See CARC code'}"
- CARC: {claim.carc_code}
- Action required: {action.upper()}

DENIAL ANALYSIS:
- Category: {analysis.denial_category if analysis else 'other'}
- Root cause: {analysis.root_cause if analysis else 'See CARC code'}
- Missing items: {analysis.missing_items if analysis else []}
- Incorrect items: {analysis.incorrect_items if analysis else []}

EVIDENCE ASSESSMENT (from evidence check):
- Evidence sufficient: {evidence.evidence_sufficient if evidence else 'unknown'}
- Key arguments to make: {key_arguments}
- Evidence gaps: {evidence_gaps}

PAYER INFORMATION:
- Payer: {payer_name}
- Portal: {portal_url or 'See payer documentation'}
- Appeal deadline: {appeal_deadline_days} days from denial date ({claim.denial_date})
- Billing guidelines: {billing_guidelines}

RELEVANT SOP PROCEDURE:
{sop_steps}

TASK: {instructions}

For CorrectionPlan: claim_id must be "{claim.claim_id}", plan_type="{action if action in ('resubmission','appeal','both') else 'appeal'}"
For AppealLetter: recipient_name="{payer_name} Appeals Department", date_of_letter="{date.today()}"

Return structured output only. Be specific, professional, and cite the key arguments above.
"""


# ------------------------------------------------------------------ #
# Fallback: rule-based response (when LLM unavailable)
# ------------------------------------------------------------------ #

def _rule_based_response(state: DenialWorkflowState) -> tuple[CorrectionPlan | None, AppealPackage | None]:
    """Generates a generic response without LLM."""
    claim = state.claim
    analysis = state.denial_analysis
    action = state.routing_decision

    correction_plan = None
    appeal_package = None

    if action in ("resubmit", "both"):
        docs = [
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
        ]
        correction_plan = CorrectionPlan(
            claim_id=claim.claim_id,
            plan_type="resubmission",
            documentation_required=docs,
            resubmission_instructions=[
                "Correct identified issues per analysis",
                "Attach all required documentation",
                "Submit within payer timely filing window",
            ],
            compliance_notes=["Verify all codes comply with current CMS guidelines"],
        )

    if action in ("appeal", "both"):
        payer_name = (
            state.enriched_data.payer_policy.payer_name
            if state.enriched_data and state.enriched_data.payer_policy
            else claim.payer_name or claim.payer_id
        )
        sender = f"Provider Billing Department (NPI: {claim.provider_id})"
        denial_reason = claim.denial_reason or f"CARC {claim.carc_code}"
        key_args = state.evidence_check.key_arguments if state.evidence_check else []

        letter = AppealLetter(
            recipient_name=f"{payer_name} Appeals Department",
            sender_name=sender,
            date_of_letter=date.today(),
            subject_line=f"Formal Appeal — Claim {claim.claim_id} — {denial_reason[:60]}",
            opening_paragraph=(
                "We are writing to formally appeal the denial of the above-referenced claim. "
                "We respectfully request reconsideration of this determination."
            ),
            denial_summary=(
                f"Claim {claim.claim_id} was denied with reason: {denial_reason}. "
                f"We believe this denial was issued in error based on the following."
            ),
            clinical_justification=(
                " ".join(key_args) if key_args else
                "Services were medically necessary per treating physician documentation."
            ),
            regulatory_basis=(
                "We request reconsideration pursuant to applicable federal and state regulations "
                "and the terms of the member's benefit plan."
            ),
            closing_paragraph=(
                "We respectfully request that you reverse this denial. "
                "Please contact our office with any questions. "
                "We expect a decision within the timeframe required by applicable law."
            ),
            signature_block=sender,
        )

        docs = [
            SupportingDocument(
                document_name="Original EOB",
                document_type="eob_copy",
                description="Copy of the Explanation of Benefits showing denial",
                is_attached=bool(claim.eob_pdf_path),
            ),
            SupportingDocument(
                document_name="Letter of Medical Necessity",
                document_type="medical_necessity_letter",
                description="Physician-signed letter explaining medical necessity",
                is_attached=False,
            ),
        ]

        payer_id = claim.payer_id
        portal_url = getattr(
            state.enriched_data.payer_policy if state.enriched_data else None,
            "appeal_portal_url", None
        )
        appeal_package = AppealPackage(
            claim_id=claim.claim_id,
            payer_id=payer_id,
            patient_id=claim.patient_id,
            appeal_letter=letter,
            supporting_documents=docs,
            denial_date=claim.denial_date,
            appeal_deadline_days=180,
            payer_appeal_portal=portal_url,
        )

    return correction_plan, appeal_package


class _ResponseOutput(Exception):
    """Internal carrier for structured LLM output."""
    def __init__(self, correction_plan, appeal_package):
        self.correction_plan = correction_plan
        self.appeal_package = appeal_package


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _run_llm_response(
    prompt: str,
    state: DenialWorkflowState,
) -> tuple[CorrectionPlan | None, AppealPackage | None]:
    """Single LLM call (call 2) that generates the full response package."""
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        from rcm_denial.config.settings import settings
        import json as _json

        action = state.routing_decision
        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.2,
            max_tokens=settings.openai_max_tokens,
            openai_api_key=settings.openai_api_key,
        )

        # ---- Gap 49: token tracking helpers ----------------------------
        _total_input = _total_output = 0

        def _invoke_tracked(sl, sub_prompt: str):
            """Invoke structured_llm and capture token counts."""
            nonlocal _total_input, _total_output
            try:
                from langchain_community.callbacks import get_openai_callback
                with get_openai_callback() as cb:
                    out = sl.invoke(sub_prompt)
                _total_input  += cb.prompt_tokens
                _total_output += cb.completion_tokens
            except ImportError:
                out            = sl.invoke(sub_prompt)
                _total_input  += max(1, len(sub_prompt) // 4)
                _total_output += 500
            return out

        # Use structured output for CorrectionPlan and/or AppealLetter separately
        correction_plan: CorrectionPlan | None = None
        appeal_package: AppealPackage | None = None

        if action in ("resubmit", "both"):
            structured_llm  = llm.with_structured_output(CorrectionPlan)
            correction_plan = _invoke_tracked(
                structured_llm,
                prompt + "\n\nRespond with a CorrectionPlan JSON only.",
            )

        if action in ("appeal", "both"):
            structured_llm = llm.with_structured_output(AppealLetter)
            letter = _invoke_tracked(
                structured_llm,
                prompt + "\n\nRespond with an AppealLetter JSON only.",
            )

            # Build AppealPackage around the letter
            enriched = state.enriched_data
            claim = state.claim
            payer_name = (
                enriched.payer_policy.payer_name
                if enriched and enriched.payer_policy
                else claim.payer_name or claim.payer_id
            )
            portal_url = (
                enriched.payer_policy.appeal_portal_url
                if enriched and enriched.payer_policy else None
            )
            appeal_deadline_days = (
                enriched.payer_policy.appeal_deadline_days
                if enriched and enriched.payer_policy else 180
            )

            docs = _build_supporting_docs(state)

            appeal_package = AppealPackage(
                claim_id=claim.claim_id,
                payer_id=claim.payer_id,
                patient_id=claim.patient_id,
                appeal_letter=letter,
                supporting_documents=docs,
                denial_date=claim.denial_date,
                appeal_deadline_days=appeal_deadline_days,
                payer_appeal_portal=portal_url,
            )

        # ---- Gap 49: persist accumulated token usage -------------------
        try:
            from rcm_denial.services.cost_tracker import record_llm_call
            from rcm_denial.config.settings import settings as _settings
            record_llm_call(
                run_id=state.run_id,
                batch_id=state.batch_id,
                agent_name=NODE_NAME,
                model=_settings.openai_model,
                input_tokens=_total_input,
                output_tokens=_total_output,
            )
        except Exception:
            pass   # cost tracking is non-fatal

        return correction_plan, appeal_package

    except ImportError:
        logger.warning("LangChain/OpenAI not available — using rule-based response")
        return _rule_based_response(state)
    except Exception as exc:
        if "rate_limit" in str(exc).lower() or "429" in str(exc):
            logger.warning("LLM rate limit — retrying", error=str(exc))
        raise


def _build_supporting_docs(state: DenialWorkflowState) -> list[SupportingDocument]:
    """Builds supporting document list from available EHR data."""
    docs = []
    enriched = state.enriched_data
    analysis = state.denial_analysis

    docs.append(SupportingDocument(
        document_name="Original EOB",
        document_type="eob_copy",
        description="Copy of the Explanation of Benefits showing denial",
        is_attached=bool(state.claim.eob_pdf_path),
    ))

    if enriched and enriched.ehr_data:
        ehr = enriched.ehr_data
        if ehr.encounter_notes:
            docs.append(SupportingDocument(
                document_name="Physician Encounter Note",
                document_type="clinical_note",
                description="Treating physician encounter note for date of service",
                is_attached=ehr.has_encounter_notes,
                source="EHR",
            ))
        if ehr.prior_auth_records:
            docs.append(SupportingDocument(
                document_name="Prior Authorization Record",
                document_type="auth_letter",
                description="Prior authorization approval letter from payer",
                is_attached=ehr.has_auth_documentation,
                source="EHR / Payer",
            ))

    docs.append(SupportingDocument(
        document_name="Letter of Medical Necessity",
        document_type="medical_necessity_letter",
        description="Physician-signed letter explaining medical necessity on letterhead",
        is_attached=False,
        source="Provider Office",
    ))

    if analysis:
        if analysis.denial_category == "coding_error":
            docs.append(SupportingDocument(
                document_name="Coding Review Summary",
                document_type="coding_review",
                description="Internal coding review confirming corrected codes",
                is_attached=False,
            ))
        if analysis.denial_category == "medical_necessity":
            docs.append(SupportingDocument(
                document_name="Clinical Guidelines Reference",
                document_type="clinical_guideline",
                description="Relevant CMS LCD/NCD or clinical guideline supporting the service",
                is_attached=False,
            ))

    return docs


# ------------------------------------------------------------------ #
# LangGraph node
# ------------------------------------------------------------------ #

def response_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Response Agent — LLM CALL 2.

    Generates the complete denial response package using the full context
    assembled by all prior agents:
      - ClaimRecord (from CSV)
      - EnrichedData (patient, payer, EHR, EOB, SOP)
      - DenialAnalysis (rule-based, from analysis_agent)
      - EvidenceCheckResult (LLM call 1, from evidence_check_agent)

    Handles all action types in a single call:
      "resubmit"  → CorrectionPlan + cover letter
      "appeal"    → AppealPackage (letter + docs)
      "both"      → CorrectionPlan + AppealPackage
      "write_off" → skips LLM, produces minimal documentation

    Args:
        state: Full workflow state.

    Returns:
        Updated state with correction_plan and/or appeal_package populated.
    """
    start = time.perf_counter()
    claim = state.claim
    action = state.routing_decision

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info(
        "Response agent starting",
        claim_id=claim.claim_id,
        action=action,
        evidence_sufficient=state.evidence_check.evidence_sufficient if state.evidence_check else "unknown",
    )

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        if action == "write_off":
            # Write-off: no LLM call needed — just document the decision
            logger.info("Write-off path — no response generation needed", claim_id=claim.claim_id)
            duration_ms = (time.perf_counter() - start) * 1000
            state.add_audit(
                NODE_NAME, "completed",
                details="Write-off path — no response generated",
                duration_ms=duration_ms,
            )
            return state

        prompt = _build_response_prompt(state)
        correction_plan, appeal_package = _run_llm_response(prompt, state)

        if correction_plan:
            state.correction_plan = correction_plan
        if appeal_package:
            state.appeal_package = appeal_package

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Action: {action} | "
                f"Correction plan: {'yes' if correction_plan else 'no'} | "
                f"Appeal package: {'yes' if appeal_package else 'no'} | "
                f"Supporting docs: {len(appeal_package.supporting_documents) if appeal_package else 0}"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Response agent complete",
            claim_id=claim.claim_id,
            action=action,
            has_correction_plan=bool(correction_plan),
            has_appeal_package=bool(appeal_package),
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Response agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        logger.error("Response agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
