##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: supervisor_router.py
# Purpose: LangGraph conditional edge function that routes
#          the workflow from analysis to the appropriate
#          next node(s) based on the recommended action.
#          Implements supervisor pattern for multi-path routing.
#
##########################################################

from __future__ import annotations

from rcm_denial.models.output import DenialWorkflowState
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


def supervisor_route(state: DenialWorkflowState) -> str:
    """
    LangGraph conditional edge function.

    Reads state.routing_decision (set by analysis_agent) and
    returns the name of the next node to execute.

    Routing logic:
        "resubmit" → correction_plan_agent
        "appeal"   → appeal_prep_agent
        "both"     → correction_plan_agent (then appeal runs after)
        "write_off"→ document_packaging_agent (skip both plan nodes)
        ""         → appeal_prep_agent (safe default)

    Note: LangGraph does not natively fan-out from a single conditional
    edge to two parallel nodes. For "both", we route to correction first,
    then the graph continues to appeal via a normal edge. This keeps the
    graph deterministic while still producing both outputs.
    """
    decision = state.routing_decision

    logger.info(
        "Supervisor routing",
        claim_id=state.claim.claim_id,
        routing_decision=decision,
    )

    route_map = {
        "resubmit": "correction_plan_agent",
        "appeal":   "appeal_prep_agent",
        "both":     "correction_plan_agent",   # correction → appeal via normal edge
        "write_off": "document_packaging_agent",
    }

    next_node = route_map.get(decision, "appeal_prep_agent")

    logger.info(
        "Supervisor routed",
        claim_id=state.claim.claim_id,
        next_node=next_node,
    )

    return next_node


def should_run_appeal_after_correction(state: DenialWorkflowState) -> str:
    """
    Secondary conditional edge after correction_plan_agent.
    If routing_decision is "both", also run appeal after correction.
    Otherwise proceed directly to document packaging.
    """
    if state.routing_decision == "both":
        return "appeal_prep_agent"
    return "document_packaging_agent"
