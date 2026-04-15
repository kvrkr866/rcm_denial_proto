##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: pipeline_reentry.py
# Purpose: Phase 4 — Pipeline re-entry for reviewed claims.
#
#          Three re-entry paths after human review:
#
#          1. re_route(run_id, stage, reviewer_notes)
#             Re-runs from a specific pipeline stage with
#             reviewer guidance injected into LLM prompts.
#             Stages: intake_agent | targeted_ehr_agent | response_agent
#
#          2. apply_human_override(run_id, response_text)
#             Replaces AI appeal letter with reviewer-written text.
#             Skips response_agent entirely — goes straight to packaging.
#
#          3. finalize_write_off(run_id)
#             Confirms write-off; produces minimal write-off documentation.
#             (Write-off was already marked in review_queue by write_off().)
#
#          After re-entry, the updated claim lands back in the queue
#          with status='re_processed' for final reviewer sign-off.
#
##########################################################

from __future__ import annotations

from rcm_denial.services.audit_service import get_logger
from rcm_denial.services.review_queue import (
    _load_state_from_queue,
    get_queue_item,
    mark_submitted,
)

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Re-route: re-run from a chosen pipeline stage
# ------------------------------------------------------------------ #

def re_route(run_id: str) -> dict:
    """
    Re-runs the pipeline from the stage chosen by the reviewer.

    Loads state from queue, injects reviewer_notes into state.human_notes,
    clears downstream outputs from the re-entry point, then re-invokes
    the appropriate pipeline segment.

    After completion the updated claim is automatically re-queued
    (review_gate_agent runs at the end of every pipeline execution).

    Returns:
        SubmissionPackage dict of the re-run result.
    """
    item = get_queue_item(run_id)
    if not item:
        raise ValueError(f"run_id {run_id!r} not found in review queue")
    if item["status"] != "re_routed":
        raise ValueError(
            f"Claim {run_id} is not in 're_routed' status (current: {item['status']}). "
            "Call review_queue.re_route() first."
        )

    stage = item["reentry_node"]
    reviewer_notes = item.get("reviewer_notes") or ""

    logger.info(
        "Pipeline re-entry starting",
        run_id=run_id,
        claim_id=item["claim_id"],
        stage=stage,
        has_notes=bool(reviewer_notes),
    )

    state = _load_state_from_queue(run_id)

    # Inject reviewer guidance into state
    state.human_notes = reviewer_notes
    state.review_count = (item.get("review_count") or 0)

    if stage == "intake_agent":
        _clear_all_outputs(state)
        return _run_full_pipeline(state)

    elif stage == "targeted_ehr_agent":
        _clear_from_targeted_ehr(state)
        return _run_from_targeted_ehr(state)

    elif stage == "response_agent":
        _clear_from_response(state)
        return _run_from_response(state)

    else:
        raise ValueError(f"Unknown re-entry stage: {stage!r}")


def _clear_all_outputs(state) -> None:
    """Clears all agent outputs — full re-run from intake."""
    state.enriched_data = None
    state.denial_analysis = None
    state.evidence_check = None
    state.correction_plan = None
    state.appeal_package = None
    state.output_package = None
    state.routing_decision = ""
    state.errors = []
    state.is_complete = False


def _clear_from_targeted_ehr(state) -> None:
    """Clears outputs from targeted_ehr_agent onward."""
    if state.enriched_data and state.enriched_data.ehr_data:
        state.enriched_data.ehr_data.diagnostic_reports = []
        state.enriched_data.ehr_data.stage2_fetched = False
    state.correction_plan = None
    state.appeal_package = None
    state.output_package = None
    state.errors = []
    state.is_complete = False


def _clear_from_response(state) -> None:
    """Clears outputs from response_agent onward."""
    state.correction_plan = None
    state.appeal_package = None
    state.output_package = None
    state.errors = []
    state.is_complete = False


def _run_full_pipeline(state) -> dict:
    from rcm_denial.workflows.denial_graph import denial_graph
    result_dict = denial_graph.invoke(state.model_dump())
    from rcm_denial.models.output import DenialWorkflowState
    final = DenialWorkflowState(**result_dict)
    return final.output_package.model_dump() if final.output_package else {}


def _run_from_targeted_ehr(state) -> dict:
    """Re-runs: targeted_ehr_agent → response_agent → packaging → review_gate."""
    from rcm_denial.agents.targeted_ehr_agent import targeted_ehr_agent
    from rcm_denial.agents.response_agent import response_agent
    from rcm_denial.agents.document_packaging_agent import document_packaging_agent
    from rcm_denial.agents.review_gate_agent import review_gate_agent

    state = targeted_ehr_agent(state)
    state = response_agent(state)
    state = document_packaging_agent(state)
    state = review_gate_agent(state)
    return state.output_package.model_dump() if state.output_package else {}


def _run_from_response(state) -> dict:
    """Re-runs: response_agent → packaging → review_gate."""
    from rcm_denial.agents.response_agent import response_agent
    from rcm_denial.agents.document_packaging_agent import document_packaging_agent
    from rcm_denial.agents.review_gate_agent import review_gate_agent

    state = response_agent(state)
    state = document_packaging_agent(state)
    state = review_gate_agent(state)
    return state.output_package.model_dump() if state.output_package else {}


# ------------------------------------------------------------------ #
# Human override: reviewer-written response → packaging
# ------------------------------------------------------------------ #

def apply_human_override(run_id: str) -> dict:
    """
    Applies the reviewer-written response text to the claim package.

    Reads override_response_text from the queue, builds a minimal
    AppealLetter from it, then re-runs document_packaging_agent
    and review_gate_agent so the claim re-enters the queue as
    're_processed' with the human-authored package.

    Returns:
        SubmissionPackage dict of the overridden result.
    """
    item = get_queue_item(run_id)
    if not item:
        raise ValueError(f"run_id {run_id!r} not found")
    if item["status"] != "human_override":
        raise ValueError(
            f"Claim {run_id} is not in 'human_override' status (current: {item['status']}). "
            "Call review_queue.human_override() first."
        )

    response_text = item.get("override_response_text") or ""
    if not response_text.strip():
        raise ValueError("override_response_text is empty")

    logger.info(
        "Applying human override",
        run_id=run_id,
        claim_id=item["claim_id"],
        text_length=len(response_text),
    )

    state = _load_state_from_queue(run_id)
    state.is_human_override = True
    state.review_count = item.get("review_count") or 1

    # Build a minimal AppealLetter from the human-written text
    _inject_override_into_state(state, response_text)

    # Re-package and re-queue
    from rcm_denial.agents.document_packaging_agent import document_packaging_agent
    from rcm_denial.agents.review_gate_agent import review_gate_agent

    state.output_package = None
    state.errors = []
    state.is_complete = False

    state = document_packaging_agent(state)
    state = review_gate_agent(state)

    return state.output_package.model_dump() if state.output_package else {}


def _inject_override_into_state(state, response_text: str) -> None:
    """
    Replaces the AI appeal letter content with reviewer-written text.
    If no appeal_package exists (resubmit-only path), creates a minimal one.
    """
    from datetime import date
    from rcm_denial.models.appeal import AppealLetter, AppealPackage, SupportingDocument

    claim = state.claim
    payer_name = (
        state.enriched_data.payer_policy.payer_name
        if state.enriched_data and state.enriched_data.payer_policy
        else claim.payer_name or claim.payer_id
    )
    sender = f"Provider Billing Department (NPI: {claim.provider_id})"

    letter = AppealLetter(
        recipient_name=f"{payer_name} Appeals Department",
        sender_name=sender,
        date_of_letter=date.today(),
        subject_line=f"Appeal — Claim {claim.claim_id} — Human-Reviewed Response",
        opening_paragraph=response_text[:500],
        denial_summary=(
            f"Claim {claim.claim_id} was denied (CARC {claim.carc_code}). "
            "This response was prepared by a billing specialist."
        ),
        clinical_justification=response_text,
        regulatory_basis=(
            "This appeal is submitted pursuant to applicable federal and state regulations "
            "and the member's benefit plan terms."
        ),
        closing_paragraph=(
            "We respectfully request reconsideration. "
            "Please contact our office with any questions."
        ),
        signature_block=sender,
    )

    docs = [
        SupportingDocument(
            document_name="Human-Reviewed Appeal Letter",
            document_type="appeal_letter",
            description="Appeal letter prepared by billing specialist",
            is_attached=True,
        )
    ]
    if claim.eob_pdf_path:
        docs.append(SupportingDocument(
            document_name="Original EOB",
            document_type="eob_copy",
            description="Explanation of Benefits showing denial",
            is_attached=True,
        ))

    portal_url = (
        state.enriched_data.payer_policy.appeal_portal_url
        if state.enriched_data and state.enriched_data.payer_policy else None
    )

    state.appeal_package = AppealPackage(
        claim_id=claim.claim_id,
        payer_id=claim.payer_id,
        patient_id=claim.patient_id,
        appeal_letter=letter,
        supporting_documents=docs,
        denial_date=claim.denial_date,
        appeal_deadline_days=180,
        payer_appeal_portal=portal_url,
    )
    state.routing_decision = "appeal"


# ------------------------------------------------------------------ #
# Write-off finalization
# ------------------------------------------------------------------ #

def finalize_write_off(run_id: str) -> dict:
    """
    Produces minimal write-off documentation for the record.
    The claim was already marked 'written_off' in review_queue.
    This generates a write-off memo PDF and updates the output package.

    Returns the updated SubmissionPackage dict (package_type='write_off').
    """
    item = get_queue_item(run_id)
    if not item:
        raise ValueError(f"run_id {run_id!r} not found")
    if item["status"] != "written_off":
        raise ValueError(
            f"Claim {run_id} is not in 'written_off' status (current: {item['status']}). "
            "Call review_queue.write_off() first."
        )

    logger.info(
        "Finalizing write-off",
        run_id=run_id,
        claim_id=item["claim_id"],
        reason=item.get("write_off_reason"),
        amount=item.get("billed_amount"),
    )

    state = _load_state_from_queue(run_id)
    state.routing_decision = "write_off"
    state.review_count = item.get("review_count") or 0

    from rcm_denial.agents.document_packaging_agent import document_packaging_agent
    state.output_package = None
    state.is_complete = False
    state = document_packaging_agent(state)

    logger.warning(
        "Write-off finalized — revenue impact",
        run_id=run_id,
        claim_id=item["claim_id"],
        amount=item.get("billed_amount"),
        reason=item.get("write_off_reason"),
    )

    return state.output_package.model_dump() if state.output_package else {}
