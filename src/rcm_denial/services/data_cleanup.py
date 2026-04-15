##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: data_cleanup.py
# Purpose: Clear demo/test data — wipe DB tables, output
#          files, and metrics. Used for demo resets.
#
#          WARNING: Destructive operation. Only for
#          development/demo environments.
#
##########################################################

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


def clear_all_data() -> dict:
    """
    Wipe all processed data: DB tables, output files, logs, metrics.

    Returns a summary dict of what was cleared.

    WARNING: This is destructive and irreversible. Only use in demo/dev.
    """
    from rcm_denial.config.settings import settings
    summary: dict[str, int | str] = {}

    # 1. Clear DB tables
    db_path = settings.data_dir / "rcm_denial.db"
    if db_path.exists():
        tables_cleared = _clear_db_tables(db_path)
        summary["db_tables_cleared"] = tables_cleared
    else:
        summary["db_tables_cleared"] = 0

    # 2. Clear output directory
    output_dir = settings.output_dir
    if output_dir.exists():
        claim_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
        for d in claim_dirs:
            shutil.rmtree(d, ignore_errors=True)
        summary["output_dirs_removed"] = len(claim_dirs)
    else:
        summary["output_dirs_removed"] = 0

    # 3. Clear metrics file
    metrics_file = settings.data_dir / "metrics" / "rcm_denial.prom"
    if metrics_file.exists():
        metrics_file.unlink()
        summary["metrics_cleared"] = True
    else:
        summary["metrics_cleared"] = False

    # 4. Clear manifest (SOP collections remain — only reset manifest tracking)
    manifest_path = settings.sop_documents_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
        summary["manifest_cleared"] = True

    logger.warning("All demo data cleared", **summary)
    return summary


def _clear_db_tables(db_path: Path) -> int:
    """Delete all rows from all application tables. Returns count of tables cleared."""
    tables = [
        "claim_intake_log",
        "claim_audit_log",
        "claim_pipeline_result",
        "claim_checkpoint",
        "human_review_queue",
        "submission_log",
        "llm_cost_log",
    ]
    cleared = 0
    try:
        with sqlite3.connect(db_path) as conn:
            for table in tables:
                try:
                    conn.execute(f"DELETE FROM {table}")
                    cleared += 1
                except Exception:
                    pass  # table may not exist yet
            conn.commit()
    except Exception as exc:
        logger.warning("DB clear failed", error=str(exc))
    return cleared


def get_audit_log_for_claim(claim_id: str, batch_id: str = "") -> list[dict]:
    """
    Retrieve audit log entries for a specific claim from the database.
    Returns list of dicts with: node_name, status, details, duration_ms, token_usage, recorded_at.
    """
    from rcm_denial.config.settings import settings
    db_path = settings.data_dir / "rcm_denial.db"
    if not db_path.exists():
        return []

    try:
        conditions = ["claim_id = ?"]
        params: list = [claim_id]
        if batch_id:
            conditions.append("batch_id = ?")
            params.append(batch_id)

        where = " AND ".join(conditions)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT node_name, status, details, duration_ms, token_usage, recorded_at
                  FROM claim_audit_log
                 WHERE {where}
                 ORDER BY id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
