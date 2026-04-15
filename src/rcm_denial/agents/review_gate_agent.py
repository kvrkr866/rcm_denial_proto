##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: review_gate_agent.py
# Purpose: Phase 4 — Non-blocking review gate node.
#
#          Runs after document_packaging_agent.
#          Writes every completed claim to human_review_queue.
#          Never blocks — the batch continues immediately.
#
#          The reviewer works through the queue independently,
#          at their own pace, using the CLI review-queue commands.
#
##########################################################

from __future__ import annotations

import time

from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import bind_claim_context, get_logger
from rcm_denial.services.review_queue import enqueue_for_review
from rcm_denial.services.review_queue_helpers import _flag_reasons, should_auto_approve

logger = get_logger(__name__)
NODE_NAME = "review_gate_agent"


def review_gate_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Review Gate — non-blocking HITL queue entry point.

    Queues every claim for human review after packaging is complete.
    The reviewer later approves, re-routes, overrides, or writes off
    via the CLI (rcm-denial review-queue ...).

    This node never raises — a failure here must not affect the main
    pipeline result. The claim's SubmissionPackage is already finalized.

    Args:
        state: Completed workflow state (output_package is set).

    Returns:
        Unchanged state (review gate is write-only to the queue).
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        flags = _flag_reasons(state)
        auto = should_auto_approve(state)

        logger.info(
            "Review gate: queuing claim",
            claim_id=claim.claim_id,
            flag_count=len(flags),
            auto_approvable=auto,
            review_count=state.review_count,
        )

        enqueue_for_review(state)

        duration_ms = (time.perf_counter() - start) * 1000
        state.add_audit(
            NODE_NAME,
            "completed",
            details=(
                f"Queued for review | "
                f"Flags: {len(flags)} | "
                f"Auto-approvable: {auto} | "
                f"Review count: {state.review_count}"
            ),
            duration_ms=duration_ms,
        )

        if flags:
            logger.info(
                "Review flags",
                claim_id=claim.claim_id,
                flags=flags,
            )
        else:
            logger.info(
                "Claim auto-approvable (use review-queue approve-all)",
                claim_id=claim.claim_id,
            )

    except Exception as exc:
        # Non-fatal: log warning but never fail the pipeline
        logger.warning(
            "Review gate enqueue failed — claim pipeline result unaffected",
            claim_id=claim.claim_id,
            error=str(exc),
        )
        state.add_audit(NODE_NAME, "failed", details=str(exc))

    return state
