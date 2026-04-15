##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: checkpoint_service.py
# Purpose: Per-node crash recovery checkpointing.
#
#          After each LangGraph node completes, the full
#          DenialWorkflowState is saved to claim_checkpoint.
#          On batch restart, claims can resume from the last
#          completed node instead of re-running from scratch.
#
#          Enable/disable via: ENABLE_CHECKPOINTING=true
#
##########################################################

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# Pipeline node execution order (must match denial_graph.py topology)
NODE_ORDER: list[str] = [
    "intake_agent",
    "enrichment_agent",
    "analysis_agent",
    "evidence_check_agent",
    "targeted_ehr_agent",
    "response_agent",
    "correction_plan_agent",
    "appeal_prep_agent",
    "document_packaging_agent",
    "review_gate_agent",
]


def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def _init_checkpoint_table() -> None:
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_checkpoint (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id            TEXT,
                run_id              TEXT    NOT NULL,
                claim_id            TEXT    NOT NULL,
                last_completed_node TEXT    NOT NULL,
                node_index          INTEGER NOT NULL,
                state_snapshot      TEXT    NOT NULL,
                status              TEXT    DEFAULT 'in_progress',
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_id, claim_id)
            )
        """)
        conn.commit()


def get_node_index(node_name: str) -> int:
    """Returns ordinal position of a node (0-based). -1 if not found."""
    try:
        return NODE_ORDER.index(node_name)
    except ValueError:
        return -1


def save_checkpoint(
    *,
    run_id: str,
    claim_id: str,
    batch_id: str,
    node_name: str,
    state_json: str,
    status: str = "in_progress",
) -> None:
    """Save or update checkpoint after a node completes successfully."""
    from rcm_denial.config.settings import settings
    if not settings.enable_checkpointing:
        return

    _init_checkpoint_table()
    node_idx = get_node_index(node_name)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO claim_checkpoint
                    (batch_id, run_id, claim_id, last_completed_node,
                     node_index, state_snapshot, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, claim_id) DO UPDATE SET
                    last_completed_node = excluded.last_completed_node,
                    node_index          = excluded.node_index,
                    state_snapshot      = excluded.state_snapshot,
                    status              = excluded.status,
                    updated_at          = excluded.updated_at
                """,
                (
                    batch_id, run_id, claim_id, node_name,
                    node_idx, state_json, status,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("Checkpoint save failed", run_id=run_id, node=node_name, error=str(exc))


def load_checkpoint(run_id: str, claim_id: str) -> Optional[dict]:
    """
    Load the last checkpoint for a claim.

    Returns:
        {"last_completed_node": str, "node_index": int, "state_snapshot": str, "status": str}
        or None if no checkpoint exists.
    """
    from rcm_denial.config.settings import settings
    if not settings.enable_checkpointing:
        return None

    _init_checkpoint_table()
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT last_completed_node, node_index, state_snapshot, status
                  FROM claim_checkpoint
                 WHERE run_id = ? AND claim_id = ?
                """,
                (run_id, claim_id),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def mark_complete(run_id: str, claim_id: str) -> None:
    """Mark a claim's checkpoint as fully complete."""
    from rcm_denial.config.settings import settings
    if not settings.enable_checkpointing:
        return

    _init_checkpoint_table()
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute(
                """
                UPDATE claim_checkpoint
                   SET status = 'complete', updated_at = ?
                 WHERE run_id = ? AND claim_id = ?
                """,
                (datetime.utcnow().isoformat(), run_id, claim_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("Checkpoint mark_complete failed", run_id=run_id, error=str(exc))


def should_skip_node(run_id: str, claim_id: str, node_name: str) -> bool:
    """
    Returns True if this node was already completed in a previous run
    (crash recovery — skip to the next unfinished node).
    """
    checkpoint = load_checkpoint(run_id, claim_id)
    if checkpoint is None:
        return False
    if checkpoint["status"] == "complete":
        return True
    saved_index = checkpoint["node_index"]
    current_index = get_node_index(node_name)
    return current_index <= saved_index


def get_checkpoint_state(run_id: str, claim_id: str) -> Optional[str]:
    """
    Returns the serialized DenialWorkflowState JSON from the last checkpoint,
    or None if no checkpoint exists.
    """
    checkpoint = load_checkpoint(run_id, claim_id)
    if checkpoint and checkpoint["status"] != "complete":
        return checkpoint["state_snapshot"]
    return None
