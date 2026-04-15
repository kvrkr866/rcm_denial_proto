##########################################################
#
# Project: RCM - Denial Management
# File name: review_queue_helpers.py
# Purpose: Helper logic for review_queue.py — separated to
#          avoid circular imports between review_queue and models.
#
##########################################################

from __future__ import annotations

# ------------------------------------------------------------------ #
# Configurable thresholds for flagging claims for review
# ------------------------------------------------------------------ #

REVIEW_AMOUNT_THRESHOLD: float  = 10_000.0   # flag if billed_amount > this
REVIEW_CONFIDENCE_THRESHOLD: float = 0.70    # flag if confidence_score < this
REVIEW_EVIDENCE_THRESHOLD: float   = 0.65    # flag if evidence confidence < this


def _flag_reasons(state) -> list[str]:
    """
    Returns a list of human-readable reasons why this claim was
    flagged for review. Empty list = no flags (auto-approved in bulk).
    """
    reasons: list[str] = []
    claim = state.claim
    analysis = state.denial_analysis
    evidence = state.evidence_check

    if claim.billed_amount > REVIEW_AMOUNT_THRESHOLD:
        reasons.append(f"High value claim: ${claim.billed_amount:,.2f}")

    if analysis and analysis.confidence_score < REVIEW_CONFIDENCE_THRESHOLD:
        reasons.append(
            f"Low analysis confidence: {analysis.confidence_score:.0%} "
            f"(threshold {REVIEW_CONFIDENCE_THRESHOLD:.0%})"
        )

    if evidence and evidence.confidence_score < REVIEW_EVIDENCE_THRESHOLD:
        reasons.append(
            f"Low evidence confidence: {evidence.confidence_score:.0%} "
            f"(threshold {REVIEW_EVIDENCE_THRESHOLD:.0%})"
        )

    if state.routing_decision == "both":
        reasons.append("Complex action: simultaneous resubmit + appeal")

    prior = getattr(claim, "prior_appeal_attempts", 0) or 0
    if prior > 0:
        reasons.append(f"Prior appeal attempt(s): {prior} — second attempt requires care")

    if (
        analysis
        and analysis.denial_category == "medical_necessity"
        and evidence
        and not evidence.evidence_sufficient
    ):
        reasons.append("Medical necessity denial with insufficient clinical evidence")

    if evidence and evidence.needs_additional_ehr_fetch and not (
        state.enriched_data
        and state.enriched_data.ehr_data
        and state.enriched_data.ehr_data.stage2_fetched
    ):
        reasons.append("Evidence check requested additional EHR data — not retrieved")

    if state.errors:
        reasons.append(f"Pipeline errors: {len(state.errors)}")

    return reasons


def should_auto_approve(state) -> bool:
    """
    Returns True if the claim can bypass manual review (low risk).

    A claim is auto-approvable when it has NO flag reasons.
    This drives the bulk-approve shortcut in the CLI.
    """
    return len(_flag_reasons(state)) == 0
