##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: intake_agent.py
# Purpose: LangGraph node that validates the ClaimRecord at the
#          start of the pipeline. Runs two types of checks:
#
#          1. DATA INTEGRITY — required fields that must be present
#             for the pipeline to produce meaningful output.
#             Failures are added to state.errors (pipeline continues).
#
#          2. BUSINESS RULE FLAGS — contextual warnings that inform
#             downstream agents (appeal deadline, prior attempts, auth).
#             Logged to audit trail only — do not add to state.errors.
#
#          ClaimInputRecord (claim_intake.py) handles all format
#          validation before a claim reaches this agent, so checks
#          here are purely business/clinical in nature.
#
##########################################################

from __future__ import annotations

import time
from datetime import date

from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger

logger = get_logger(__name__)

NODE_NAME = "intake_agent"


def intake_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Intake Agent.

    Validates the ClaimRecord and logs contextual business flags.
    Never aborts the pipeline — errors and warnings are recorded
    and downstream agents adapt accordingly.

    Args:
        state: Current workflow state with claim populated.

    Returns:
        Updated state with audit entry, errors, and business flags noted.
    """
    start = time.perf_counter()
    claim = state.claim
    today = date.today()

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)

    logger.info(
        "Intake agent starting",
        claim_id=claim.claim_id,
        patient=claim.patient_name or claim.patient_id,
        payer=claim.payer_name or claim.payer_id,
        carc_code=claim.carc_code,
        billed_amount=claim.billed_amount,
        priority=claim.priority_label,
        specialty=claim.specialty,
    )

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    # ---------------------------------------------------------------- #
    # SECTION 1: Data integrity checks
    # Problems here mean the pipeline will have degraded output.
    # Added to state.errors — pipeline still continues.
    # ---------------------------------------------------------------- #

    data_errors: list[str] = []

    # CPT codes
    if not claim.cpt_codes:
        data_errors.append("No CPT codes present — cannot determine procedure context")

    # Diagnosis codes
    if not claim.diagnosis_codes:
        data_errors.append("No diagnosis codes present — cannot determine clinical context")

    # CARC code — primary routing signal for the pipeline
    if not claim.carc_code:
        data_errors.append("CARC code missing — denial reason unknown, routing will use default")
    elif "-" in claim.carc_code:
        # Sanity check: should have been stripped by ClaimInputRecord
        data_errors.append(
            f"CARC code still has group prefix '{claim.carc_code}' — normalization may have failed"
        )

    # Billed amount
    if claim.billed_amount <= 0:
        data_errors.append(f"Invalid billed amount: ${claim.billed_amount:,.2f}")

    # Date ordering
    if claim.denial_date < claim.date_of_service:
        data_errors.append(
            f"Denial date {claim.denial_date} precedes date of service "
            f"{claim.date_of_service} — data integrity issue"
        )

    # Patient identifier
    if not claim.patient_id:
        data_errors.append("Patient ID (member_id) missing — enrichment tools may fail")

    # Provider identifier
    if not claim.provider_id:
        data_errors.append("Provider ID (NPI) missing — EHR lookup will fail")

    # EOB path present but file doesn't exist
    if claim.eob_pdf_path:
        from pathlib import Path
        eob_path = Path(claim.eob_pdf_path)
        if not eob_path.exists():
            data_errors.append(
                f"EOB PDF path '{claim.eob_pdf_path}' does not exist — OCR will use mock data"
            )

    # Add data errors to pipeline state
    for err in data_errors:
        state.add_error(f"[data] {err}")

    if data_errors:
        logger.warning(
            "Claim has data integrity issues",
            claim_id=claim.claim_id,
            issue_count=len(data_errors),
            issues=data_errors,
        )

    # ---------------------------------------------------------------- #
    # SECTION 2: Business rule flags
    # These are contextual signals — not errors, but important context
    # for analysis, correction, and appeal agents.
    # Logged to audit trail only (not added to state.errors).
    # ---------------------------------------------------------------- #

    business_flags: list[str] = []

    # Appeal deadline tracking
    if claim.appeal_deadline:
        if claim.appeal_deadline < today:
            days_past = (today - claim.appeal_deadline).days
            business_flags.append(
                f"DEADLINE PASSED: Appeal deadline was {claim.appeal_deadline} "
                f"({days_past} days ago) — write-off may be the only option"
            )
            logger.warning(
                "Appeal deadline has passed",
                claim_id=claim.claim_id,
                deadline=str(claim.appeal_deadline),
                days_past=days_past,
            )
        elif claim.days_to_deadline is not None and claim.days_to_deadline <= 30:
            business_flags.append(
                f"DEADLINE APPROACHING: {claim.days_to_deadline} days remaining "
                f"(deadline: {claim.appeal_deadline}) — prioritize this claim"
            )
            logger.warning(
                "Appeal deadline approaching",
                claim_id=claim.claim_id,
                days_remaining=claim.days_to_deadline,
                deadline=str(claim.appeal_deadline),
            )

    # Prior auth requirement — analysis agent needs this
    if claim.requires_auth is True:
        business_flags.append(
            "Prior authorization required — analysis agent should check auth records"
        )
        logger.info(
            "Prior auth required flag noted",
            claim_id=claim.claim_id,
            requires_auth=True,
        )

    # Prior appeal history — affects strategy
    if claim.prior_appeal_attempts and claim.prior_appeal_attempts > 0:
        business_flags.append(
            f"Prior appeal attempts: {claim.prior_appeal_attempts} — "
            f"escalation or 'both' strategy may be needed"
        )
        logger.info(
            "Prior appeal attempts noted",
            claim_id=claim.claim_id,
            prior_attempts=claim.prior_appeal_attempts,
        )

    # denial_reason is None — expected at this stage, just confirm
    if not claim.denial_reason:
        business_flags.append(
            "denial_reason is empty — will be populated from EOB OCR in enrichment stage"
        )

    # Not appealable / not rebillable
    if claim.appealable is False and claim.rebillable is False:
        business_flags.append(
            "Claim marked as neither appealable nor rebillable — write-off likely"
        )

    # Claim status = Submitted (not yet officially denied in payer system)
    if claim.status and claim.status.lower() == "submitted":
        business_flags.append(
            f"Claim status is '{claim.status}' — may still be pending payer adjudication"
        )

    # Appeal win probability context
    if claim.appeal_win_probability is not None:
        prob_pct = round(claim.appeal_win_probability * 100, 1)
        if prob_pct < 25:
            business_flags.append(
                f"Low appeal win probability ({prob_pct}%) — consider cost-benefit analysis"
            )
        elif prob_pct >= 65:
            business_flags.append(
                f"High appeal win probability ({prob_pct}%) — strong candidate for appeal"
            )

    if business_flags:
        logger.info(
            "Business rule flags noted",
            claim_id=claim.claim_id,
            flag_count=len(business_flags),
            flags=business_flags,
        )

    # ---------------------------------------------------------------- #
    # Audit log — single entry capturing full intake summary
    # ---------------------------------------------------------------- #

    duration_ms = (time.perf_counter() - start) * 1000

    audit_details = (
        f"Data errors: {len(data_errors)} | "
        f"Business flags: {len(business_flags)} | "
        f"CARC: {claim.carc_code} | "
        f"Payer: {claim.payer_name or claim.payer_id} | "
        f"Specialty: {claim.specialty or 'N/A'} | "
        f"Priority: {claim.priority_label or 'N/A'} | "
        f"Days to deadline: {claim.days_to_deadline or 'N/A'} | "
        f"Requires auth: {claim.requires_auth} | "
        f"Prior appeals: {claim.prior_appeal_attempts}"
    )

    state.add_audit(
        NODE_NAME,
        "completed",
        details=audit_details,
        duration_ms=duration_ms,
    )

    logger.info(
        "Intake agent complete",
        claim_id=claim.claim_id,
        data_errors=len(data_errors),
        business_flags=len(business_flags),
        duration_ms=round(duration_ms, 2),
    )

    return state
