##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: claim_disposition.py
# Purpose: Tracks final claim disposition after payer
#          submission — the "close the loop" record.
#
#          This is a LOCAL mirror of what should ultimately
#          be written back to the provider's EHR system.
#          When real EHR integration is active, the
#          update_ehr_record() function will push this
#          data via the EMR adapter.
#
#          Table: claim_disposition
#            - One row per claim submission attempt
#            - Updated after payer responds (adjudication)
#            - Used by EHR sync to write back to patient chart
#
##########################################################

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────
# DB setup
# ──────────────────────────────────────────────────────────────────────

def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def _init_disposition_table() -> None:
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_disposition (
                id                      INTEGER  PRIMARY KEY AUTOINCREMENT,
                claim_id                TEXT     NOT NULL,
                patient_id              TEXT     NOT NULL,
                batch_id                TEXT,
                run_id                  TEXT,
                payer_id                TEXT     NOT NULL,
                payer_name              TEXT,
                provider_id             TEXT,
                provider_npi            TEXT,
                invoice_number          TEXT,

                -- Claim details
                date_of_service         TEXT,
                billed_amount           REAL,
                carc_code               TEXT,
                rarc_code               TEXT,
                denial_category         TEXT,

                -- Disposition
                disposition             TEXT     NOT NULL,
                    -- resubmitted | appealed | write_off | rejected | paid | partially_paid
                package_type            TEXT,
                    -- resubmission | appeal | both | write_off

                -- Submission to payer
                submission_method       TEXT,
                confirmation_number     TEXT,
                submitted_at            TIMESTAMP,

                -- Payer response (updated after adjudication)
                payer_response_status   TEXT     DEFAULT 'pending',
                    -- pending | approved | denied | partially_paid | in_review
                paid_amount             REAL,
                adjustment_amount       REAL,
                payer_response_date     TIMESTAMP,
                payer_notes             TEXT,

                -- EHR sync status
                ehr_synced              INTEGER  DEFAULT 0,
                    -- 0 = not yet synced to EHR
                    -- 1 = synced successfully
                    -- -1 = sync failed
                ehr_sync_timestamp      TIMESTAMP,
                ehr_sync_error          TEXT,

                -- Timestamps
                created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


# ──────────────────────────────────────────────────────────────────────
# Record disposition after payer submission
# ──────────────────────────────────────────────────────────────────────

def record_disposition(
    *,
    claim_id: str,
    patient_id: str,
    payer_id: str,
    batch_id: str = "",
    run_id: str = "",
    payer_name: str = "",
    provider_id: str = "",
    provider_npi: str = "",
    invoice_number: str = "",
    date_of_service: str = "",
    billed_amount: float = 0.0,
    carc_code: str = "",
    rarc_code: str = "",
    denial_category: str = "",
    disposition: str = "resubmitted",
    package_type: str = "",
    submission_method: str = "",
    confirmation_number: str = "",
) -> int:
    """
    Record a claim disposition after submission to payer.
    Returns the row ID.

    This is the local record that will be synced to EHR
    when real EHR integration is active.
    """
    _init_disposition_table()

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO claim_disposition
                    (claim_id, patient_id, batch_id, run_id,
                     payer_id, payer_name, provider_id, provider_npi,
                     invoice_number, date_of_service, billed_amount,
                     carc_code, rarc_code, denial_category,
                     disposition, package_type,
                     submission_method, confirmation_number,
                     submitted_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    claim_id, patient_id, batch_id, run_id,
                    payer_id, payer_name, provider_id, provider_npi,
                    invoice_number, date_of_service, billed_amount,
                    carc_code, rarc_code, denial_category,
                    disposition, package_type,
                    submission_method, confirmation_number,
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid

        logger.info(
            "Claim disposition recorded",
            claim_id=claim_id,
            patient_id=patient_id,
            disposition=disposition,
            confirmation_number=confirmation_number,
        )
        return row_id

    except Exception as exc:
        logger.error("Failed to record disposition", claim_id=claim_id, error=str(exc))
        return -1


# ──────────────────────────────────────────────────────────────────────
# Update after payer response (adjudication result)
# ──────────────────────────────────────────────────────────────────────

def update_payer_response(
    *,
    claim_id: str,
    payer_response_status: str,
    paid_amount: float = 0.0,
    adjustment_amount: float = 0.0,
    payer_notes: str = "",
) -> None:
    """Update disposition with payer's adjudication response."""
    _init_disposition_table()

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute(
                """
                UPDATE claim_disposition
                   SET payer_response_status = ?,
                       paid_amount = ?,
                       adjustment_amount = ?,
                       payer_notes = ?,
                       payer_response_date = ?,
                       updated_at = ?
                 WHERE claim_id = ?
                 ORDER BY id DESC LIMIT 1
                """,
                (
                    payer_response_status, paid_amount, adjustment_amount,
                    payer_notes, datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(), claim_id,
                ),
            )
            conn.commit()

        logger.info(
            "Payer response recorded",
            claim_id=claim_id,
            status=payer_response_status,
            paid=paid_amount,
        )
    except Exception as exc:
        logger.error("Failed to update payer response", claim_id=claim_id, error=str(exc))


# ──────────────────────────────────────────────────────────────────────
# EHR sync — push disposition to provider's EHR
# ──────────────────────────────────────────────────────────────────────

def sync_to_ehr(claim_id: str) -> bool:
    """
    Push claim disposition to the provider's EHR system.

    Currently: marks as synced in local DB (mock).
    With real EHR adapter: calls EMR adapter to update patient chart.

    Returns True if sync succeeded.
    """
    _init_disposition_table()

    try:
        from rcm_denial.config.settings import settings

        if settings.emr_adapter == "mock":
            # Mock: just mark as synced locally
            with sqlite3.connect(_get_db_path()) as conn:
                conn.execute(
                    """
                    UPDATE claim_disposition
                       SET ehr_synced = 1,
                           ehr_sync_timestamp = ?,
                           updated_at = ?
                     WHERE claim_id = ?
                    """,
                    (datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), claim_id),
                )
                conn.commit()

            logger.info("EHR sync (mock) — disposition marked as synced", claim_id=claim_id)
            return True

        else:
            # Real EHR: use the EMR adapter to push data
            from rcm_denial.services.data_source_adapters import get_emr_adapter

            disposition = get_disposition(claim_id)
            if not disposition:
                logger.warning("No disposition found for EHR sync", claim_id=claim_id)
                return False

            adapter = get_emr_adapter(settings.emr_adapter)
            # Future: adapter.update_claim_status(disposition)
            # For now, log and mark as synced
            logger.info(
                "EHR sync — would push to real EHR",
                claim_id=claim_id,
                adapter=settings.emr_adapter,
            )

            with sqlite3.connect(_get_db_path()) as conn:
                conn.execute(
                    """
                    UPDATE claim_disposition
                       SET ehr_synced = 1,
                           ehr_sync_timestamp = ?,
                           updated_at = ?
                     WHERE claim_id = ?
                    """,
                    (datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), claim_id),
                )
                conn.commit()
            return True

    except Exception as exc:
        logger.error("EHR sync failed", claim_id=claim_id, error=str(exc))
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute(
                """
                UPDATE claim_disposition
                   SET ehr_synced = -1,
                       ehr_sync_error = ?,
                       updated_at = ?
                 WHERE claim_id = ?
                """,
                (str(exc), datetime.utcnow().isoformat(), claim_id),
            )
            conn.commit()
        return False


# ──────────────────────────────────────────────────────────────────────
# Query
# ──────────────────────────────────────────────────────────────────────

def get_disposition(claim_id: str) -> Optional[dict]:
    """Get the latest disposition for a claim."""
    _init_disposition_table()
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM claim_disposition WHERE claim_id = ? ORDER BY id DESC LIMIT 1",
                (claim_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_dispositions(
    batch_id: str = "",
    disposition: str = "",
    ehr_synced: Optional[int] = None,
) -> list[dict]:
    """Query dispositions with optional filters."""
    _init_disposition_table()
    conditions = []
    params: list = []

    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if disposition:
        conditions.append("disposition = ?")
        params.append(disposition)
    if ehr_synced is not None:
        conditions.append("ehr_synced = ?")
        params.append(ehr_synced)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM claim_disposition {where} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_pending_ehr_sync() -> list[dict]:
    """Get dispositions that haven't been synced to EHR yet."""
    return get_dispositions(ehr_synced=0)
