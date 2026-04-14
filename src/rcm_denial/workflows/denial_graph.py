##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: denial_graph.py
# Purpose: Defines and compiles the LangGraph StateGraph for
#          the full denial management workflow. All nodes,
#          edges, and conditional routing are assembled here.
#          Exposes a compiled graph and a convenience function
#          process_claim() as the public API.
#
##########################################################

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from rcm_denial.agents.intake_agent import intake_agent
from rcm_denial.agents.enrichment_agent import enrichment_agent
from rcm_denial.agents.analysis_agent import analysis_agent
from rcm_denial.agents.correction_plan_agent import correction_plan_agent
from rcm_denial.agents.appeal_prep_agent import appeal_prep_agent
from rcm_denial.agents.document_packaging_agent import document_packaging_agent
from rcm_denial.models.claim import ClaimRecord
from rcm_denial.models.output import DenialWorkflowState, SubmissionPackage
from rcm_denial.services.audit_service import get_logger
from rcm_denial.workflows.supervisor_router import (
    should_run_appeal_after_correction,
    supervisor_route,
)

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Node wrapper: converts Pydantic model state ↔ dict for LangGraph
# ------------------------------------------------------------------ #

def _wrap_node(agent_fn):
    """
    LangGraph nodes receive and return plain dicts.
    This wrapper converts dict → DenialWorkflowState → dict
    so all agent functions can work with typed Pydantic models.
    """
    def wrapped(state_dict: dict) -> dict:
        state = DenialWorkflowState(**state_dict)
        updated_state = agent_fn(state)
        return updated_state.model_dump()
    wrapped.__name__ = agent_fn.__name__
    return wrapped


# ------------------------------------------------------------------ #
# Router wrappers (same dict convention)
# ------------------------------------------------------------------ #

def _supervisor_route_dict(state_dict: dict) -> str:
    state = DenialWorkflowState(**state_dict)
    return supervisor_route(state)


def _should_run_appeal_dict(state_dict: dict) -> str:
    state = DenialWorkflowState(**state_dict)
    return should_run_appeal_after_correction(state)


# ------------------------------------------------------------------ #
# Build and compile the graph
# ------------------------------------------------------------------ #

def build_denial_graph():
    """
    Constructs the LangGraph StateGraph for denial management.

    Graph topology:
        START
          └─► intake_agent
                └─► enrichment_agent
                      └─► analysis_agent
                            └─► [supervisor_route]
                                  ├─ "resubmit"  → correction_plan_agent
                                  │                   └─► document_packaging_agent
                                  ├─ "both"      → correction_plan_agent
                                  │                   └─► appeal_prep_agent
                                  │                         └─► document_packaging_agent
                                  ├─ "appeal"    → appeal_prep_agent
                                  │                   └─► document_packaging_agent
                                  └─ "write_off" → document_packaging_agent
                                                        └─► END
    """
    graph = StateGraph(dict)

    # ---- Register nodes ----
    graph.add_node("intake_agent",              _wrap_node(intake_agent))
    graph.add_node("enrichment_agent",          _wrap_node(enrichment_agent))
    graph.add_node("analysis_agent",            _wrap_node(analysis_agent))
    graph.add_node("correction_plan_agent",     _wrap_node(correction_plan_agent))
    graph.add_node("appeal_prep_agent",         _wrap_node(appeal_prep_agent))
    graph.add_node("document_packaging_agent",  _wrap_node(document_packaging_agent))

    # ---- Linear edges (fixed path) ----
    graph.add_edge(START,               "intake_agent")
    graph.add_edge("intake_agent",      "enrichment_agent")
    graph.add_edge("enrichment_agent",  "analysis_agent")

    # ---- Supervisor conditional edge (after analysis) ----
    graph.add_conditional_edges(
        "analysis_agent",
        _supervisor_route_dict,
        {
            "correction_plan_agent":    "correction_plan_agent",
            "appeal_prep_agent":        "appeal_prep_agent",
            "document_packaging_agent": "document_packaging_agent",
        },
    )

    # ---- After correction: optionally run appeal too (for "both") ----
    graph.add_conditional_edges(
        "correction_plan_agent",
        _should_run_appeal_dict,
        {
            "appeal_prep_agent":        "appeal_prep_agent",
            "document_packaging_agent": "document_packaging_agent",
        },
    )

    # ---- Appeal always leads to packaging ----
    graph.add_edge("appeal_prep_agent", "document_packaging_agent")

    # ---- Packaging is always the final node ----
    graph.add_edge("document_packaging_agent", END)

    return graph.compile()


# Module-level compiled graph singleton
denial_graph = build_denial_graph()


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def process_claim(claim_data: dict | ClaimRecord, batch_id: str = "") -> SubmissionPackage:
    """
    Public entry point for processing a single denied claim.

    Pluggable into any existing application with minimal changes:
        from rcm_denial.workflows.denial_graph import process_claim
        result = process_claim(claim_dict)

    Args:
        claim_data: Either a ClaimRecord model or a raw dict matching
                    the ClaimRecord schema (e.g. from CSV row).
        batch_id:   Optional batch identifier for run_id generation.

    Returns:
        SubmissionPackage with output paths, status, and summary.
    """
    if isinstance(claim_data, dict):
        claim = ClaimRecord(**claim_data)
    else:
        claim = claim_data

    logger.info(
        "Processing claim",
        claim_id=claim.claim_id,
        carc_code=claim.carc_code,
        payer_id=claim.payer_id,
    )

    initial_state = DenialWorkflowState.create(claim, batch_id=batch_id)

    # LangGraph requires dict input/output
    result_dict = denial_graph.invoke(initial_state.model_dump())

    final_state = DenialWorkflowState(**result_dict)

    if final_state.output_package:
        return final_state.output_package

    # Fallback if packaging agent didn't produce a package
    from rcm_denial.models.output import SubmissionPackage
    return SubmissionPackage(
        claim_id=claim.claim_id,
        run_id=initial_state.run_id,
        output_dir="",
        package_type="failed",
        status="failed",
        summary=f"Processing failed. Errors: {final_state.errors}",
    )
