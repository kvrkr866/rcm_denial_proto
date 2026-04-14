##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: appeal_prep_agent.py
# Purpose: LangGraph node that generates a complete appeal
#          package including a formal appeal letter, evidence
#          bundle, deadline tracking, and payer-specific
#          submission instructions.
#
##########################################################

from __future__ import annotations

import time
from datetime import date

from tenacity import retry, stop_after_attempt, wait_exponential

from rcm_denial.models.appeal import AppealLetter, AppealPackage, SupportingDocument
from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)
NODE_NAME = "appeal_prep_agent"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def _generate_appeal_letter_llm(prompt: str) -> dict:
    """Calls LLM to generate appeal letter sections as a dict."""
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.output_parsers import JsonOutputParser
        from rcm_denial.config.settings import settings

        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.2,
            max_tokens=settings.openai_max_tokens,
            openai_api_key=settings.openai_api_key,
        )
        chain = llm | JsonOutputParser()
        return chain.invoke(prompt)
    except ImportError:
        return _fallback_letter_sections()
    except Exception as exc:
        if "rate_limit" in str(exc).lower():
            logger.warning("LLM rate limit — retrying", error=str(exc))
        raise


def _fallback_letter_sections() -> dict:
    return {
        "subject_line": "Formal Appeal of Denied Medical Claim",
        "opening_paragraph": (
            "We are writing to formally appeal the denial of the above-referenced claim. "
            "We respectfully request a review and reconsideration of this determination."
        ),
        "denial_summary": (
            "Our records indicate this claim was denied. We believe this denial was issued "
            "in error and does not accurately reflect the medical necessity and appropriateness "
            "of the services rendered."
        ),
        "clinical_justification": (
            "The services provided were medically necessary and appropriate for the patient's "
            "diagnosis and clinical condition. All services were rendered in accordance with "
            "accepted standards of medical practice."
        ),
        "regulatory_basis": (
            "We request reconsideration pursuant to applicable federal and state regulations "
            "governing the appeals process for insured healthcare claims."
        ),
        "closing_paragraph": (
            "We respectfully request that you reconsider and reverse this denial. "
            "Please contact our office with any questions regarding this appeal. "
            "We expect a decision within the timeframe required by applicable law."
        ),
        "signature_block": "Respectfully submitted,\nBilling Department\nProvider Office",
    }


def _build_appeal_prompt(state: DenialWorkflowState) -> str:
    claim = state.claim
    analysis = state.denial_analysis
    enriched = state.enriched_data

    patient_name = "Patient"
    if enriched and enriched.patient_data:
        p = enriched.patient_data
        patient_name = f"{p.first_name} {p.last_name}"

    payer_name = claim.payer_id
    appeal_address = None
    if enriched and enriched.payer_policy:
        payer_name = enriched.payer_policy.payer_name
        appeal_address = enriched.payer_policy.appeal_address

    ehr_summary = ""
    if enriched and enriched.ehr_data:
        ehr = enriched.ehr_data
        if ehr.encounter_notes:
            ehr_summary = ehr.encounter_notes[0].content_summary
        if ehr.procedure_details:
            ehr_summary += " " + ehr.procedure_details[0].content_summary

    sop_guidance = ""
    if enriched and enriched.sop_results:
        sop_guidance = enriched.sop_results[0].content_snippet[:300]

    return f"""You are a medical billing appeals specialist. Write a formal insurance appeal letter.

CLAIM DETAILS:
- Claim ID: {claim.claim_id}
- Patient: {patient_name}
- Payer: {payer_name}
- Date of Service: {claim.date_of_service}
- CPT Codes: {', '.join(claim.cpt_codes)}
- Diagnosis Codes: {', '.join(claim.diagnosis_codes)}
- Billed Amount: ${claim.billed_amount:,.2f}
- Denial Reason: {claim.denial_reason}
- CARC Code: {claim.carc_code}

DENIAL ANALYSIS:
- Root Cause: {analysis.root_cause if analysis else 'See denial reason'}
- Reasoning: {analysis.reasoning if analysis else 'Manual review required'}

CLINICAL DOCUMENTATION AVAILABLE:
{ehr_summary or 'See attached medical records'}

RELEVANT SOP GUIDANCE:
{sop_guidance or 'Follow standard appeal procedures'}

Write a professional, persuasive appeal letter. Return ONLY a JSON object with these keys:
- subject_line: one-line RE: subject
- opening_paragraph: formal opening requesting reconsideration
- denial_summary: brief factual summary of denial and why it is incorrect
- clinical_justification: clinical argument citing diagnosis and procedure necessity
- regulatory_basis: cite relevant regulations, CMS guidelines, or plan terms
- closing_paragraph: professional closing requesting expedited review
- signature_block: professional signature block

Return valid JSON only. No markdown, no preamble.
"""


def _build_supporting_docs(state: DenialWorkflowState) -> list[SupportingDocument]:
    """Builds the supporting document list based on available EHR data."""
    docs = []
    enriched = state.enriched_data
    analysis = state.denial_analysis

    # Always include EOB copy
    docs.append(SupportingDocument(
        document_name="Original EOB",
        document_type="eob_copy",
        description="Copy of the Explanation of Benefits showing denial",
        is_attached=bool(state.claim.eob_pdf_path),
    ))

    if not enriched:
        return docs

    # Encounter notes
    if enriched.ehr_data and enriched.ehr_data.encounter_notes:
        docs.append(SupportingDocument(
            document_name="Physician Encounter Note",
            document_type="clinical_note",
            description="Treating physician's encounter note for date of service",
            is_attached=enriched.ehr_data.has_encounter_notes,
            source="EHR",
        ))

    # Prior auth records
    if enriched.ehr_data and enriched.ehr_data.prior_auth_records:
        docs.append(SupportingDocument(
            document_name="Prior Authorization Record",
            document_type="auth_letter",
            description="Prior authorization approval letter from payer",
            is_attached=enriched.ehr_data.has_auth_documentation,
            source="EHR / Payer",
        ))

    # Medical necessity letter (always recommended for appeals)
    docs.append(SupportingDocument(
        document_name="Letter of Medical Necessity",
        document_type="medical_necessity_letter",
        description="Physician-signed letter explaining medical necessity on letterhead",
        is_attached=False,
        source="Provider Office",
    ))

    # Category-specific docs
    if analysis and analysis.denial_category == "coding_error":
        docs.append(SupportingDocument(
            document_name="Coding Review Summary",
            document_type="coding_review",
            description="Internal coding review confirming corrected codes",
            is_attached=False,
        ))

    if analysis and analysis.denial_category == "medical_necessity":
        docs.append(SupportingDocument(
            document_name="Clinical Guidelines Reference",
            document_type="clinical_guideline",
            description="Relevant CMS LCD/NCD or clinical guideline supporting the service",
            is_attached=False,
        ))

    return docs


def appeal_prep_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Appeal Preparation Agent.
    Generates formal appeal letter, evidence bundle, and deadline tracking.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info("Appeal prep agent starting", claim_id=claim.claim_id)

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        # Get payer details for letter and deadline
        appeal_deadline_days = 180
        appeal_address = None
        appeal_fax = None
        appeal_portal = None
        payer_name = claim.payer_id
        submission_instructions = []

        if state.enriched_data and state.enriched_data.payer_policy:
            py = state.enriched_data.payer_policy
            appeal_deadline_days = py.appeal_deadline_days
            appeal_address = py.appeal_address
            appeal_fax = py.appeal_fax
            appeal_portal = py.appeal_portal_url
            payer_name = py.payer_name
            submission_instructions = py.notes[:3]

        # Generate letter via LLM
        prompt = _build_appeal_prompt(state)
        letter_sections = _generate_appeal_letter_llm(prompt)

        # Sender info
        sender_name = f"Provider Billing Department (Provider ID: {claim.provider_id})"

        appeal_letter = AppealLetter(
            recipient_name=f"{payer_name} Appeals Department",
            recipient_address=appeal_address,
            sender_name=sender_name,
            date_of_letter=date.today(),
            subject_line=letter_sections.get(
                "subject_line",
                f"Appeal of Denied Claim — Claim ID: {claim.claim_id}",
            ),
            opening_paragraph=letter_sections.get("opening_paragraph", ""),
            denial_summary=letter_sections.get("denial_summary", ""),
            clinical_justification=letter_sections.get("clinical_justification", ""),
            regulatory_basis=letter_sections.get("regulatory_basis", ""),
            closing_paragraph=letter_sections.get("closing_paragraph", ""),
            signature_block=letter_sections.get("signature_block", sender_name),
        )

        supporting_docs = _build_supporting_docs(state)

        # Add submission instructions
        if appeal_portal:
            submission_instructions.append(f"Submit via payer portal: {appeal_portal}")
        if appeal_fax:
            submission_instructions.append(f"Fax submissions to: {appeal_fax}")
        if appeal_address:
            submission_instructions.append(f"Mail submissions to: {appeal_address}")

        package = AppealPackage(
            claim_id=claim.claim_id,
            payer_id=claim.payer_id,
            patient_id=claim.patient_id,
            appeal_letter=appeal_letter,
            supporting_documents=supporting_docs,
            denial_date=claim.denial_date,
            appeal_deadline_days=appeal_deadline_days,
            payer_appeal_portal=appeal_portal,
            payer_appeal_fax=appeal_fax,
            payer_appeal_address=appeal_address,
            submission_instructions=submission_instructions,
        )

        state.appeal_package = package

        duration_ms = (time.perf_counter() - start) * 1000
        attached = sum(1 for d in supporting_docs if d.is_attached)
        state.add_audit(
            NODE_NAME, "completed",
            details=(
                f"Appeal letter generated, "
                f"{len(supporting_docs)} supporting docs ({attached} attached), "
                f"Deadline: {package.appeal_deadline}, "
                f"Urgent: {package.is_urgent}"
            ),
            duration_ms=duration_ms,
        )
        logger.info(
            "Appeal prep complete",
            claim_id=claim.claim_id,
            deadline=str(package.appeal_deadline),
            days_remaining=package.days_until_deadline,
            is_urgent=package.is_urgent,
            duration_ms=round(duration_ms, 2),
        )

    except Exception as exc:
        state.add_error(f"Appeal prep agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        logger.error("Appeal prep agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
