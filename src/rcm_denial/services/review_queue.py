##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: review_queue.py
# Purpose: Phase 4 — Non-blocking human review queue.
#
#          Every claim completes the full pipeline first.
#          After document_packaging_agent, review_gate_agent
#          writes the claim to this queue.
#
#          Reviewer actions (async, independent of batch):
#            approve        → triggers Phase 5 submission
#            re_route       → re-runs pipeline from chosen stage
#            human_override → reviewer-written response replaces AI output
#            write_off      → last resort; guarded by review_count check
#
#          Write-off guard:
#            Blocked unless review_count >= 1 OR timely filing expired.
#            Tracked as a revenue-loss metric.
#
##########################################################

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)

# Valid re-entry stages for human re-route
VALID_REENTRY_STAGES = Literal["intake_agent", "targeted_ehr_agent", "response_agent"]

# Write-off reasons presented to reviewer
WRITE_OFF_REASONS = [
    "timely_filing_expired",
    "cost_exceeds_recovery",
    "payer_non_negotiable",
    "duplicate_confirmed_paid",
    "patient_responsibility",
    "other",
]


# ------------------------------------------------------------------ #
# DB helpers
# ------------------------------------------------------------------ #

def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def _init_queue_db() -> None:
    """Creates human_review_queue table if not exists."""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS human_review_queue (
                id                      INTEGER  PRIMARY KEY AUTOINCREMENT,
                run_id                  TEXT     NOT NULL UNIQUE,
                batch_id                TEXT,
                claim_id                TEXT     NOT NULL,
                status                  TEXT     NOT NULL DEFAULT 'pending',
                    -- pending | approved | re_routed | re_processed
                    -- | human_override | written_off | submitted
                package_type            TEXT,
                billed_amount           REAL,
                carc_code               TEXT,
                denial_category         TEXT,
                routing_decision        TEXT,
                confidence_score        REAL,
                evidence_confidence     REAL,
                prior_appeal_attempts   INTEGER  DEFAULT 0,
                review_count            INTEGER  DEFAULT 0,
                ai_summary              TEXT,
                reviewer_notes          TEXT,
                reentry_node            TEXT,
                override_response_text  TEXT,
                write_off_reason        TEXT,
                write_off_notes         TEXT,
                reviewed_by             TEXT,
                reviewed_at             TIMESTAMP,
                state_snapshot          TEXT,    -- full DenialWorkflowState JSON
                output_dir              TEXT,
                pdf_package_path        TEXT,
                days_to_appeal_deadline INTEGER,
                is_urgent               INTEGER  DEFAULT 0,
                created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


# ------------------------------------------------------------------ #
# AI summary generator
# ------------------------------------------------------------------ #

def _build_ai_summary(state) -> str:
    """
    Generates a concise decision-ready summary for the human reviewer.
    Explains the denial analysis, evidence found, and why the claim was queued.
    """
    from rcm_denial.services.review_queue_helpers import _flag_reasons
    claim = state.claim
    analysis = state.denial_analysis
    evidence = state.evidence_check
    pkg = state.output_package

    lines = [
        f"CLAIM: {claim.claim_id}  |  Payer: {claim.payer_name or claim.payer_id}  "
        f"|  CARC: {claim.carc_code}  |  Amount: ${claim.billed_amount:,.2f}",
        f"Category: {analysis.denial_category if analysis else 'unknown'}  "
        f"|  Action: {(state.routing_decision or 'unknown').upper()}  "
        f"|  Package: {(pkg.package_type if pkg else 'none').upper()}",
        "",
    ]

    if analysis:
        lines.append(f"Root cause: {analysis.root_cause}")
        if analysis.missing_items:
            lines.append(f"Missing: {', '.join(analysis.missing_items[:3])}")

    if evidence:
        lines.append(
            f"Evidence confidence: {evidence.confidence_score:.0%}  "
            f"|  Sufficient: {'Yes' if evidence.evidence_sufficient else 'No'}"
        )
        if evidence.key_arguments:
            lines.append("Key arguments:")
            for arg in evidence.key_arguments[:3]:
                lines.append(f"  • {arg}")
        if evidence.evidence_gaps:
            lines.append(f"Evidence gaps: {'; '.join(evidence.evidence_gaps[:2])}")

    if pkg:
        lines.append(f"Package status: {pkg.status.upper()}")
        if pkg.pdf_package_path:
            lines.append(f"Package: {pkg.pdf_package_path}")

    flags = _flag_reasons(state)
    if flags:
        lines.append("")
        lines.append("Flagged for review because:")
        for f in flags:
            lines.append(f"  ⚑ {f}")

    return "\n".join(lines)


# ------------------------------------------------------------------ #
# Enqueue
# ------------------------------------------------------------------ #

def enqueue_for_review(state) -> None:
    """
    Writes a completed claim to the human_review_queue.
    Called by review_gate_agent after document_packaging_agent.

    Upserts on run_id so re-processed claims update the existing row
    rather than creating duplicates.
    """
    _init_queue_db()

    claim = state.claim
    analysis = state.denial_analysis
    evidence = state.evidence_check
    pkg = state.output_package

    ai_summary = _build_ai_summary(state)
    state_json = state.model_dump_json()

    prior_attempts = getattr(claim, "prior_appeal_attempts", 0) or 0
    days_to_deadline = getattr(claim, "days_to_deadline", None)
    is_urgent = int(getattr(claim, "is_urgent", False))

    evidence_confidence = evidence.confidence_score if evidence else None
    analysis_confidence = analysis.confidence_score if analysis else None

    new_status = "re_processed" if state.review_count > 0 else "pending"

    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO human_review_queue
                (run_id, batch_id, claim_id, status,
                 package_type, billed_amount, carc_code,
                 denial_category, routing_decision,
                 confidence_score, evidence_confidence,
                 prior_appeal_attempts, review_count,
                 ai_summary, state_snapshot,
                 output_dir, pdf_package_path,
                 days_to_appeal_deadline, is_urgent,
                 updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(run_id) DO UPDATE SET
                status              = excluded.status,
                package_type        = excluded.package_type,
                confidence_score    = excluded.confidence_score,
                evidence_confidence = excluded.evidence_confidence,
                review_count        = excluded.review_count,
                ai_summary          = excluded.ai_summary,
                state_snapshot      = excluded.state_snapshot,
                output_dir          = excluded.output_dir,
                pdf_package_path    = excluded.pdf_package_path,
                updated_at          = CURRENT_TIMESTAMP
            """,
            (
                state.run_id,
                state.batch_id,
                claim.claim_id,
                new_status,
                pkg.package_type if pkg else "failed",
                claim.billed_amount,
                claim.carc_code,
                analysis.denial_category if analysis else "unknown",
                state.routing_decision,
                analysis_confidence,
                evidence_confidence,
                prior_attempts,
                state.review_count,
                ai_summary,
                state_json,
                pkg.output_dir if pkg else "",
                pkg.pdf_package_path if pkg else None,
                days_to_deadline,
                is_urgent,
            ),
        )
        conn.commit()

    logger.info(
        "Claim enqueued for review",
        run_id=state.run_id,
        claim_id=claim.claim_id,
        status=new_status,
        review_count=state.review_count,
    )


# ------------------------------------------------------------------ #
# Read operations
# ------------------------------------------------------------------ #

def get_queue(
    batch_id: str = "",
    status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Returns queue items, optionally filtered by batch and/or status."""
    _init_queue_db()
    conditions = []
    params: list = []
    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, run_id, batch_id, claim_id, status,
                   package_type, billed_amount, carc_code,
                   denial_category, routing_decision,
                   confidence_score, evidence_confidence,
                   prior_appeal_attempts, review_count,
                   days_to_appeal_deadline, is_urgent,
                   reviewed_by, reviewed_at, created_at
            FROM human_review_queue
            {where}
            ORDER BY is_urgent DESC, billed_amount DESC, created_at ASC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_queue_count(batch_id: str = "", status: str = "") -> int:
    """Returns total count of queue items matching filters."""
    _init_queue_db()
    conditions = []
    params: list = []
    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with sqlite3.connect(_get_db_path()) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM human_review_queue {where}", params
        ).fetchone()
    return row[0] if row else 0


def get_queue_item(run_id: str) -> dict | None:
    """Returns full detail for a single queue item including ai_summary."""
    _init_queue_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM human_review_queue WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def _load_state_from_queue(run_id: str):
    """Deserializes the stored DenialWorkflowState from a queue row."""
    from rcm_denial.models.output import DenialWorkflowState
    item = get_queue_item(run_id)
    if not item:
        raise ValueError(f"run_id {run_id!r} not found in review queue")
    return DenialWorkflowState.model_validate_json(item["state_snapshot"])


# ------------------------------------------------------------------ #
# Reviewer actions
# ------------------------------------------------------------------ #

def approve(run_id: str, reviewer: str = "human") -> dict:
    """
    Marks a claim as approved.
    The Phase 5 submission adapter will then submit it to the payer portal.
    """
    _init_queue_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            UPDATE human_review_queue
               SET status = 'approved',
                   reviewed_by = ?,
                   reviewed_at = CURRENT_TIMESTAMP,
                   updated_at  = CURRENT_TIMESTAMP
             WHERE run_id = ?
            """,
            (reviewer, run_id),
        )
        conn.commit()
    logger.info("Claim approved", run_id=run_id, reviewer=reviewer)
    item = get_queue_item(run_id)
    return item or {}


def re_route(
    run_id: str,
    stage: str,
    notes: str = "",
    reviewer: str = "human",
) -> dict:
    """
    Routes the claim back into the pipeline at the specified stage.

    stage must be one of:
      'intake_agent'       — re-process from scratch (claim data was wrong)
      'targeted_ehr_agent' — fetch more clinical evidence
      'response_agent'     — regenerate letter/plan with reviewer guidance

    notes are injected into the re-run LLM prompts via state.human_notes.
    """
    valid = ("intake_agent", "targeted_ehr_agent", "response_agent")
    if stage not in valid:
        raise ValueError(f"Invalid stage {stage!r}. Must be one of: {valid}")

    _init_queue_db()
    with sqlite3.connect(_get_db_path()) as conn:
        row = conn.execute(
            "SELECT review_count FROM human_review_queue WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"run_id {run_id!r} not found")
        new_review_count = (row[0] or 0) + 1

        conn.execute(
            """
            UPDATE human_review_queue
               SET status        = 're_routed',
                   reentry_node  = ?,
                   reviewer_notes= ?,
                   review_count  = ?,
                   reviewed_by   = ?,
                   reviewed_at   = CURRENT_TIMESTAMP,
                   updated_at    = CURRENT_TIMESTAMP
             WHERE run_id = ?
            """,
            (stage, notes, new_review_count, reviewer, run_id),
        )
        conn.commit()

    logger.info(
        "Claim re-routed",
        run_id=run_id,
        stage=stage,
        review_count=new_review_count,
        reviewer=reviewer,
    )
    return get_queue_item(run_id) or {}


def human_override(
    run_id: str,
    response_text: str,
    reviewer: str = "human",
) -> dict:
    """
    Replaces the AI-generated response with reviewer-written text.
    The text will be embedded into the appeal package and packaged for submission.
    """
    if not response_text.strip():
        raise ValueError("response_text cannot be empty for human override")

    _init_queue_db()
    with sqlite3.connect(_get_db_path()) as conn:
        row = conn.execute(
            "SELECT review_count FROM human_review_queue WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"run_id {run_id!r} not found")
        new_review_count = (row[0] or 0) + 1

        conn.execute(
            """
            UPDATE human_review_queue
               SET status                 = 'human_override',
                   override_response_text = ?,
                   review_count           = ?,
                   reviewed_by            = ?,
                   reviewed_at            = CURRENT_TIMESTAMP,
                   updated_at             = CURRENT_TIMESTAMP
             WHERE run_id = ?
            """,
            (response_text, new_review_count, reviewer, run_id),
        )
        conn.commit()

    logger.info("Human override applied", run_id=run_id, reviewer=reviewer)
    return get_queue_item(run_id) or {}


def write_off(
    run_id: str,
    reason: str,
    notes: str = "",
    reviewer: str = "human",
    force: bool = False,
) -> dict:
    """
    Marks a claim as written off (last resort).

    Guard: blocked unless review_count >= 1 (re-route was attempted first)
    OR reason == 'timely_filing_expired' (no path forward).
    Set force=True to bypass the guard (manager override).

    reason must be one of WRITE_OFF_REASONS.
    """
    if reason not in WRITE_OFF_REASONS:
        raise ValueError(f"Invalid write-off reason. Must be one of: {WRITE_OFF_REASONS}")

    _init_queue_db()
    with sqlite3.connect(_get_db_path()) as conn:
        row = conn.execute(
            "SELECT review_count, billed_amount, claim_id FROM human_review_queue WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"run_id {run_id!r} not found")

        review_count, billed_amount, claim_id = row

        # Guard: must have attempted re-route at least once, unless filing expired or forced
        if not force and reason != "timely_filing_expired" and (review_count or 0) < 1:
            raise PermissionError(
                f"Write-off blocked for {claim_id} (${billed_amount:,.2f}): "
                f"attempt re-route first, or use reason='timely_filing_expired'. "
                f"Use force=True for manager override."
            )

        conn.execute(
            """
            UPDATE human_review_queue
               SET status          = 'written_off',
                   write_off_reason= ?,
                   write_off_notes = ?,
                   reviewer_notes  = ?,
                   reviewed_by     = ?,
                   reviewed_at     = CURRENT_TIMESTAMP,
                   updated_at      = CURRENT_TIMESTAMP
             WHERE run_id = ?
            """,
            (reason, notes, notes, reviewer, run_id),
        )
        conn.commit()

    logger.warning(
        "Claim written off",
        run_id=run_id,
        claim_id=claim_id,
        billed_amount=billed_amount,
        reason=reason,
        reviewer=reviewer,
    )
    return get_queue_item(run_id) or {}


def mark_submitted(run_id: str) -> None:
    """Called by Phase 5 submission adapter after successful portal submission."""
    _init_queue_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            UPDATE human_review_queue
               SET status     = 'submitted',
                   updated_at = CURRENT_TIMESTAMP
             WHERE run_id = ?
            """,
            (run_id,),
        )
        conn.commit()


def bulk_approve(
    batch_id: str = "",
    confidence_above: float = 0.85,
    amount_below: float = 5000.0,
    reviewer: str = "human",
) -> int:
    """
    Bulk-approves low-risk pending claims in one operation.

    Approves claims where:
      confidence_score >= confidence_above AND billed_amount <= amount_below

    Returns count of claims approved.
    """
    _init_queue_db()
    conditions = [
        "status = 'pending'",
        "confidence_score >= ?",
        "billed_amount <= ?",
    ]
    params: list = [confidence_above, amount_below]

    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)

    params.extend([reviewer])

    where = " AND ".join(conditions)
    with sqlite3.connect(_get_db_path()) as conn:
        result = conn.execute(
            f"""
            UPDATE human_review_queue
               SET status     = 'approved',
                   reviewed_by= ?,
                   reviewed_at= CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
             WHERE {where}
            """,
            [reviewer] + [confidence_above, amount_below] + ([batch_id] if batch_id else []),
        )
        conn.commit()
        count = result.rowcount

    logger.info(
        "Bulk approval complete",
        batch_id=batch_id,
        confidence_above=confidence_above,
        amount_below=amount_below,
        claims_approved=count,
    )
    return count


# ------------------------------------------------------------------ #
# Statistics — revenue impact metrics
# ------------------------------------------------------------------ #

def get_review_stats(batch_id: str = "") -> dict:
    """
    Returns HITL metrics AND eval quality signals for a batch (or all-time).

    Standard metrics:
      write_off_count, write_off_total_amount, write_off_preventable,
      recovery_rate_pct, avg_review_cycles, human_override_count

    Eval quality signals (derived from reviewer actions as ground truth):
      first_pass_approval_rate_pct  — % approved with review_count=0 (AI got it right first time)
      override_rate_pct             — % claims needing human rewrite (strong failure signal)
      reroute_by_stage              — which pipeline stage gets re-routed most
      confidence_calibration        — avg confidence for approved vs re-routed claims
      multi_cycle_claim_ids         — exact claim_ids needing >1 review cycle (your eval dataset)
    """
    _init_queue_db()
    where = "WHERE batch_id = ?" if batch_id else ""
    params = [batch_id] if batch_id else []

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM human_review_queue {where}",
            params,
        ).fetchall()

    if not rows:
        return {"message": "No queue data found", "batch_id": batch_id}

    total = len(rows)
    status_counts: dict[str, int] = {}
    write_off_amount = 0.0
    write_off_preventable = 0
    reprocessed_then_approved = 0
    total_review_cycles = 0
    human_override_count = 0

    # Eval signal accumulators
    first_pass_approved = 0       # review_count=0 and status in approved/submitted
    reroute_by_stage: dict[str, int] = {}
    multi_cycle_claim_ids: list[str] = []
    conf_approved: list[float] = []    # confidence scores of approved claims
    conf_rerouted: list[float] = []    # confidence scores of re-routed claims

    for r in rows:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
        review_count = r["review_count"] or 0
        conf = r["confidence_score"]

        if s == "written_off":
            write_off_amount += r["billed_amount"] or 0.0
            if r["write_off_reason"] != "timely_filing_expired":
                write_off_preventable += 1

        if review_count > 0 and s in ("approved", "submitted"):
            reprocessed_then_approved += 1

        total_review_cycles += review_count

        if s == "human_override":
            human_override_count += 1

        # Eval: first-pass approval
        if review_count == 0 and s in ("approved", "submitted"):
            first_pass_approved += 1

        # Eval: re-route stage distribution
        if s == "re_routed" and r["reentry_node"]:
            stage = r["reentry_node"]
            reroute_by_stage[stage] = reroute_by_stage.get(stage, 0) + 1

        # Eval: multi-cycle claims (strong signal of AI failure)
        if review_count > 1:
            multi_cycle_claim_ids.append(r["claim_id"])

        # Eval: confidence calibration
        if conf is not None:
            if s in ("approved", "submitted") and review_count == 0:
                conf_approved.append(conf)
            elif s in ("re_routed", "human_override"):
                conf_rerouted.append(conf)

    write_off_count = status_counts.get("written_off", 0)
    approved_count = status_counts.get("approved", 0) + status_counts.get("submitted", 0)
    override_rate = round(human_override_count / max(total, 1) * 100, 1)
    first_pass_rate = round(first_pass_approved / max(total, 1) * 100, 1)

    avg_conf_approved = round(sum(conf_approved) / len(conf_approved), 3) if conf_approved else None
    avg_conf_rerouted = round(sum(conf_rerouted) / len(conf_rerouted), 3) if conf_rerouted else None

    return {
        "batch_id":                     batch_id or "all",
        "total_claims":                 total,
        "status_breakdown":             status_counts,
        "approved":                     approved_count,
        "pending":                      status_counts.get("pending", 0),
        "re_routed":                    status_counts.get("re_routed", 0),
        "re_processed":                 status_counts.get("re_processed", 0),
        "human_override_count":         human_override_count,
        "write_off_count":              write_off_count,
        "write_off_total_amount":       round(write_off_amount, 2),
        "write_off_preventable":        write_off_preventable,
        "recovery_count":               reprocessed_then_approved,
        "recovery_rate_pct":            round(reprocessed_then_approved / max(total, 1) * 100, 1),
        "avg_review_cycles":            round(total_review_cycles / max(total, 1), 2),
        "write_off_revenue_impact":     f"${write_off_amount:,.2f}",
        # ── Eval quality signals ──────────────────────────────────────
        "first_pass_approval_rate_pct": first_pass_rate,
        "override_rate_pct":            override_rate,
        "reroute_by_stage":             reroute_by_stage,
        "multi_cycle_claim_ids":        sorted(set(multi_cycle_claim_ids)),
        "confidence_calibration": {
            "avg_conf_approved_first_pass": avg_conf_approved,
            "avg_conf_rerouted":            avg_conf_rerouted,
            "gap": (
                round(avg_conf_approved - avg_conf_rerouted, 3)
                if avg_conf_approved is not None and avg_conf_rerouted is not None
                else None
            ),
            "note": (
                "Positive gap = confidence is well-calibrated "
                "(approved claims scored higher than re-routed ones)"
            ),
        },
    }
