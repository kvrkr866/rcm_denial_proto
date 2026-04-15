##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: denial_graph.py
# Purpose: Defines and compiles the LangGraph StateGraph for
#          the full denial management workflow.
#
#          LLM call budget: max 2 per claim (3 on complex cases)
#            Call 1 — evidence_check_agent
#            Call 2 — response_agent
#            Call 3 — (optional) triggered when needs_additional_ehr_fetch=True
#
#          Graph topology:
#            START
#              └─► intake_agent
#                    └─► enrichment_agent       (sets denial_reason from EOB)
#                          └─► analysis_agent   (rule-based, no LLM)
#                                └─► [supervisor_route]
#                                      ├─ write_off → document_packaging_agent
#                                      └─► evidence_check_agent  (LLM call 1)
#                                                └─► [stage2_route]
#                                                      ├─ needs_fetch=False → response_agent
#                                                      └─ needs_fetch=True  → targeted_ehr_agent
#                                                                                └─► response_agent
#                                                                                      └─► document_packaging_agent
#                                                                                            └─► END
#
##########################################################

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from rcm_denial.agents.intake_agent import intake_agent
from rcm_denial.agents.enrichment_agent import enrichment_agent
from rcm_denial.agents.analysis_agent import analysis_agent
from rcm_denial.agents.evidence_check_agent import evidence_check_agent
from rcm_denial.agents.targeted_ehr_agent import targeted_ehr_agent
from rcm_denial.agents.response_agent import response_agent
from rcm_denial.agents.document_packaging_agent import document_packaging_agent
from rcm_denial.agents.review_gate_agent import review_gate_agent
from rcm_denial.models.claim import ClaimRecord
from rcm_denial.models.output import DenialWorkflowState, SubmissionPackage
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Node wrapper: Pydantic model ↔ dict for LangGraph
# ------------------------------------------------------------------ #

def _wrap_node(agent_fn):
    def wrapped(state_dict: dict) -> dict:
        state = DenialWorkflowState(**state_dict)
        updated_state = agent_fn(state)
        return updated_state.model_dump()
    wrapped.__name__ = agent_fn.__name__
    return wrapped


# ------------------------------------------------------------------ #
# Supervisor router — after analysis_agent
# Routes write_off directly to packaging; everything else to evidence_check
# ------------------------------------------------------------------ #

def _supervisor_route(state_dict: dict) -> str:
    state = DenialWorkflowState(**state_dict)
    decision = state.routing_decision

    logger.info(
        "Supervisor routing",
        claim_id=state.claim.claim_id,
        routing_decision=decision,
    )

    if decision == "write_off":
        next_node = "document_packaging_agent"
    else:
        next_node = "evidence_check_agent"

    logger.info("Supervisor routed", claim_id=state.claim.claim_id, next_node=next_node)
    return next_node


# ------------------------------------------------------------------ #
# Stage 2 router — after evidence_check_agent
# Triggers targeted EHR fetch only when evidence gaps require it.
# ------------------------------------------------------------------ #

def _stage2_route(state_dict: dict) -> str:
    state = DenialWorkflowState(**state_dict)
    evidence = state.evidence_check

    if evidence and evidence.needs_additional_ehr_fetch:
        next_node = "targeted_ehr_agent"
        logger.info(
            "Stage 2 EHR fetch required",
            claim_id=state.claim.claim_id,
            fetch_description=evidence.additional_fetch_description,
        )
    else:
        next_node = "response_agent"

    return next_node


# ------------------------------------------------------------------ #
# Build and compile the graph
# ------------------------------------------------------------------ #

def build_denial_graph():
    graph = StateGraph(dict)

    # ---- Register nodes ----
    graph.add_node("intake_agent",             _wrap_node(intake_agent))
    graph.add_node("enrichment_agent",         _wrap_node(enrichment_agent))
    graph.add_node("analysis_agent",           _wrap_node(analysis_agent))
    graph.add_node("evidence_check_agent",     _wrap_node(evidence_check_agent))
    graph.add_node("targeted_ehr_agent",       _wrap_node(targeted_ehr_agent))
    graph.add_node("response_agent",           _wrap_node(response_agent))
    graph.add_node("document_packaging_agent", _wrap_node(document_packaging_agent))
    graph.add_node("review_gate_agent",        _wrap_node(review_gate_agent))

    # ---- Linear path ----
    graph.add_edge(START,               "intake_agent")
    graph.add_edge("intake_agent",      "enrichment_agent")
    graph.add_edge("enrichment_agent",  "analysis_agent")

    # ---- Supervisor: write_off → packaging, everything else → evidence_check ----
    graph.add_conditional_edges(
        "analysis_agent",
        _supervisor_route,
        {
            "evidence_check_agent":     "evidence_check_agent",
            "document_packaging_agent": "document_packaging_agent",
        },
    )

    # ---- Stage 2 router: needs_fetch → targeted_ehr_agent, else → response_agent ----
    graph.add_conditional_edges(
        "evidence_check_agent",
        _stage2_route,
        {
            "targeted_ehr_agent": "targeted_ehr_agent",
            "response_agent":     "response_agent",
        },
    )

    # ---- Targeted EHR → response (Stage 2 path only) ----
    graph.add_edge("targeted_ehr_agent",       "response_agent")

    # ---- Response → packaging → review gate → END ----
    graph.add_edge("response_agent",           "document_packaging_agent")
    graph.add_edge("document_packaging_agent", "review_gate_agent")
    graph.add_edge("review_gate_agent",        END)

    return graph.compile()


# Module-level compiled graph singleton
denial_graph = build_denial_graph()


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def process_claim(claim_data: dict | ClaimRecord, batch_id: str = "") -> SubmissionPackage:
    """
    Public entry point for processing a single denied claim.

    Args:
        claim_data: ClaimRecord or raw dict matching ClaimRecord schema.
        batch_id:   Optional batch identifier.

    Returns:
        SubmissionPackage with output paths, status, and summary.
    """
    from rcm_denial.services.claim_intake import persist_audit_log, persist_pipeline_result

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

    start = time.perf_counter()
    initial_state = DenialWorkflowState.create(claim, batch_id=batch_id)
    result_dict = denial_graph.invoke(initial_state.model_dump())
    final_state = DenialWorkflowState(**result_dict)
    duration_ms = (time.perf_counter() - start) * 1000

    # ---- Persist audit log (Gap 41) ----
    try:
        persist_audit_log(
            batch_id=batch_id,
            run_id=initial_state.run_id,
            claim_id=claim.claim_id,
            audit_entries=final_state.audit_log,
        )
    except Exception as exc:
        logger.warning("Audit log persistence failed", claim_id=claim.claim_id, error=str(exc))

    # ---- Persist pipeline result for statistics (Gap 43) ----
    try:
        analysis = final_state.denial_analysis
        pkg = final_state.output_package
        # LLM call accounting:
        #   Call 1 — evidence_check_agent (always, unless write_off)
        #   Call 2 — response_agent (unless write_off)
        #   Stage 2 path adds targeted_ehr_agent (no LLM, but an extra node)
        llm_calls = 0
        if final_state.evidence_check:
            llm_calls += 1   # LLM call 1
        if final_state.routing_decision != "write_off" and (
            final_state.appeal_package or final_state.correction_plan
        ):
            llm_calls += 1   # LLM call 2

        persist_pipeline_result(
            batch_id=batch_id,
            run_id=initial_state.run_id,
            claim_id=claim.claim_id,
            carc_code=claim.carc_code or "",
            denial_category=analysis.denial_category if analysis else "unknown",
            recommended_action=final_state.routing_decision or "unknown",
            final_status=pkg.status if pkg else "failed",
            package_type=pkg.package_type if pkg else "failed",
            errors=[e.details for e in final_state.audit_log if e.status == "failed"],
            pipeline_errors=final_state.errors,
            duration_ms=duration_ms,
            llm_calls=llm_calls,
        )
    except Exception as exc:
        logger.warning("Pipeline result persistence failed", claim_id=claim.claim_id, error=str(exc))

    if final_state.output_package:
        return final_state.output_package

    return SubmissionPackage(
        claim_id=claim.claim_id,
        run_id=initial_state.run_id,
        output_dir="",
        package_type="failed",
        status="failed",
        summary=f"Processing failed. Errors: {final_state.errors}",
    )
