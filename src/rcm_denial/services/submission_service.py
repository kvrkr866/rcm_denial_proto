##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: submission_service.py
# Purpose: Phase 5 — Orchestrates payer portal submission.
#
#          Gap 25: Logs every attempt to submission_log table.
#          Gap 26: Retry logic via tenacity (exponential backoff).
#          Gap 27: Called after human approval from review queue.
#
#          Main entry points:
#            submit_approved_claim(run_id)  — submit one approved claim
#            submit_approved_batch(batch_id) — submit all approved in batch
#            check_submission_status(run_id) — poll payer for adjudication
#            get_submission_log(run_id)      — read attempt history
#
##########################################################

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rcm_denial.models.submission import SubmissionResult, SubmissionStatus
from rcm_denial.services.audit_service import get_logger
from rcm_denial.services.review_queue import (
    _load_state_from_queue,
    get_queue,
    get_queue_item,
    mark_submitted,
)
from rcm_denial.services.submission_adapters import get_submission_adapter

logger = get_logger(__name__)


def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


# ------------------------------------------------------------------ #
# Gap 25 — Submission log helpers
# ------------------------------------------------------------------ #

def _log_attempt(
    *,
    run_id: str,
    claim_id: str,
    batch_id: str,
    payer_id: str,
    submission_method: str,
    attempt_number: int,
    result: SubmissionResult,
    package_type: str = "",
    pdf_path: str = "",
) -> None:
    """Inserts one row into submission_log for every attempt (success or fail)."""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO submission_log
                (run_id, claim_id, batch_id, payer_id, submission_method,
                 attempt_number, status, response_code, response_message,
                 confirmation_number, package_type, pdf_path,
                 submitted_at, response_received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, claim_id, batch_id, payer_id, submission_method,
                attempt_number,
                "submitted" if result.success else "failed",
                result.response_code,
                result.response_message,
                result.confirmation_number,
                package_type,
                pdf_path,
                result.submitted_at.isoformat(),
                result.response_received_at.isoformat() if result.response_received_at else None,
            ),
        )
        conn.commit()


def get_submission_log(run_id: str) -> list[dict]:
    """Returns all submission attempts for a claim, newest first."""
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM submission_log
             WHERE run_id = ?
             ORDER BY attempt_number DESC
            """,
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_submission_stats(batch_id: str = "") -> dict:
    """Aggregated submission metrics for a batch or all time."""
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    where = "WHERE batch_id = ?" if batch_id else ""
    params = [batch_id] if batch_id else []

    with sqlite3.connect(_get_db_path()) as conn:
        rows = conn.execute(
            f"SELECT status, COUNT(*) as cnt, SUM(attempt_number) as attempts "
            f"FROM submission_log {where} GROUP BY status",
            params,
        ).fetchall()

    stats: dict = {"batch_id": batch_id or "all_time", "status_counts": {}}
    for row in rows:
        stats["status_counts"][row[0]] = {"count": row[1], "total_attempts": row[2]}
    return stats


# ------------------------------------------------------------------ #
# Gap 26 — Retry wrapper (tenacity)
# ------------------------------------------------------------------ #

class _SubmissionTransientError(Exception):
    """Raised for retryable submission failures (network errors, 5xx)."""
    pass


def _make_retry_decorator():
    from rcm_denial.config.settings import settings
    return retry(
        stop=stop_after_attempt(settings.submission_max_retries),
        wait=wait_exponential(
            multiplier=1,
            min=settings.submission_retry_delay_seconds,
            max=settings.submission_retry_delay_seconds * 8,
        ),
        retry=retry_if_exception_type(_SubmissionTransientError),
        reraise=True,
    )


def _attempt_submit(
    adapter,
    package,
    state,
    run_id: str,
    attempt_number: int,
) -> SubmissionResult:
    """
    Single submission attempt — wrapped by tenacity retry in submit_approved_claim().
    Raises _SubmissionTransientError for retryable failures (network/5xx).
    Returns SubmissionResult on success or permanent failure.
    """
    payer_id = state.claim.payer_id
    try:
        result = adapter.submit(package, state)
        _log_attempt(
            run_id=run_id,
            claim_id=package.claim_id,
            batch_id=state.batch_id,
            payer_id=payer_id,
            submission_method=result.submission_method or type(adapter).__name__,
            attempt_number=attempt_number,
            result=result,
            package_type=package.package_type,
            pdf_path=package.pdf_package_path or "",
        )
        return result

    except NotImplementedError:
        # Scaffold not yet implemented — log and return structured failure
        result = SubmissionResult(
            success=False,
            submission_method=type(adapter).__name__,
            response_code="NOT_IMPLEMENTED",
            response_message=(
                f"{type(adapter).__name__} is a scaffold — implement submit() "
                "for live payer integration."
            ),
            attempt_number=attempt_number,
            submitted_at=datetime.utcnow(),
            error_detail="Adapter not implemented",
        )
        _log_attempt(
            run_id=run_id,
            claim_id=package.claim_id,
            batch_id=state.batch_id,
            payer_id=payer_id,
            submission_method=type(adapter).__name__,
            attempt_number=attempt_number,
            result=result,
            package_type=package.package_type,
            pdf_path=package.pdf_package_path or "",
        )
        return result   # permanent failure — do NOT retry

    except Exception as exc:
        err_str = str(exc).lower()
        # Classify retryable errors
        is_transient = any(kw in err_str for kw in (
            "timeout", "connection", "network", "502", "503", "504", "rate_limit", "429",
        ))
        result = SubmissionResult(
            success=False,
            submission_method=type(adapter).__name__,
            response_code="ERROR",
            response_message=str(exc),
            attempt_number=attempt_number,
            submitted_at=datetime.utcnow(),
            error_detail=str(exc),
        )
        _log_attempt(
            run_id=run_id,
            claim_id=package.claim_id,
            batch_id=state.batch_id,
            payer_id=payer_id,
            submission_method=type(adapter).__name__,
            attempt_number=attempt_number,
            result=result,
        )
        if is_transient:
            raise _SubmissionTransientError(str(exc)) from exc
        return result   # permanent failure


# ------------------------------------------------------------------ #
# Gap 27 — Main submission orchestrator
# ------------------------------------------------------------------ #

def submit_approved_claim(run_id: str) -> SubmissionResult:
    """
    Submits one approved claim to its payer portal.

    Flow:
      1. Load state + package from review queue (must be status='approved')
      2. Resolve submission adapter from payer_submission_registry
      3. Attempt submission with exponential backoff retry (Gap 26)
      4. Log every attempt to submission_log (Gap 25)
      5. On success: mark queue item as 'submitted'
      6. Returns SubmissionResult

    Raises:
        ValueError: if run_id not found or claim is not in 'approved' status.
    """
    item = get_queue_item(run_id)
    if not item:
        raise ValueError(f"run_id {run_id!r} not found in review queue")

    status = item["status"]
    if status not in ("approved", "re_processed"):
        raise ValueError(
            f"Claim {run_id} is not approved for submission (status: {status!r}). "
            "Approve via: rcm-denial review-queue approve --run-id {run_id}"
        )

    state = _load_state_from_queue(run_id)
    package = state.output_package

    if not package:
        raise ValueError(f"No output package found in state for run_id {run_id!r}")

    payer_id = state.claim.payer_id
    adapter = get_submission_adapter(payer_id)

    logger.info(
        "Submitting claim",
        run_id=run_id,
        claim_id=package.claim_id,
        payer_id=payer_id,
        adapter=type(adapter).__name__,
        package_type=package.package_type,
    )

    # Build a retry-wrapped attempt function for this call
    retry_dec = _make_retry_decorator()
    attempt_counter = [0]

    @retry_dec
    def _submit_with_retry() -> SubmissionResult:
        attempt_counter[0] += 1
        return _attempt_submit(adapter, package, state, run_id, attempt_counter[0])

    try:
        result = _submit_with_retry()
    except _SubmissionTransientError as exc:
        # All retries exhausted
        result = SubmissionResult(
            success=False,
            submission_method=type(adapter).__name__,
            response_code="RETRY_EXHAUSTED",
            response_message=f"All {attempt_counter[0]} attempts failed: {exc}",
            attempt_number=attempt_counter[0],
            submitted_at=datetime.utcnow(),
            error_detail=str(exc),
        )

    if result.success:
        mark_submitted(run_id)
        logger.info(
            "Claim submitted successfully",
            run_id=run_id,
            claim_id=package.claim_id,
            confirmation_number=result.confirmation_number,
            method=result.submission_method,
        )
    else:
        logger.error(
            "Claim submission failed",
            run_id=run_id,
            claim_id=package.claim_id,
            response_code=result.response_code,
            message=result.response_message,
            attempts=attempt_counter[0],
        )

    return result


def submit_approved_batch(batch_id: str) -> dict:
    """
    Submits all approved claims in a batch.

    Returns a summary dict:
      {submitted: N, failed: N, skipped: N, results: [{run_id, claim_id, success, ...}]}

    Continues on individual claim failure — never aborts the batch.
    """
    items = get_queue(batch_id=batch_id, status="approved")
    if not items:
        logger.info("No approved claims to submit", batch_id=batch_id)
        return {"submitted": 0, "failed": 0, "skipped": 0, "results": []}

    logger.info(
        "Batch submission starting",
        batch_id=batch_id,
        claim_count=len(items),
    )

    submitted, failed, skipped = 0, 0, 0
    results = []

    for item in items:
        run_id = item["run_id"]
        try:
            result = submit_approved_claim(run_id)
            if result.success:
                submitted += 1
            else:
                failed += 1
            results.append({
                "run_id":              run_id,
                "claim_id":            item["claim_id"],
                "success":             result.success,
                "confirmation_number": result.confirmation_number,
                "method":              result.submission_method,
                "response_code":       result.response_code,
                "message":             result.response_message,
            })
        except Exception as exc:
            failed += 1
            logger.error(
                "Batch submission error",
                run_id=run_id,
                claim_id=item.get("claim_id"),
                error=str(exc),
            )
            results.append({
                "run_id":   run_id,
                "claim_id": item.get("claim_id"),
                "success":  False,
                "error":    str(exc),
            })

    logger.info(
        "Batch submission complete",
        batch_id=batch_id,
        submitted=submitted,
        failed=failed,
        skipped=skipped,
    )
    return {
        "batch_id":  batch_id,
        "submitted": submitted,
        "failed":    failed,
        "skipped":   skipped,
        "results":   results,
    }


def check_submission_status(run_id: str) -> SubmissionStatus:
    """
    Polls the payer for adjudication status of a submitted claim.

    Reads the confirmation_number from the submission_log,
    then calls adapter.check_status().
    """
    logs = get_submission_log(run_id)
    successful = [l for l in logs if l["status"] == "submitted" and l["confirmation_number"]]
    if not successful:
        raise ValueError(
            f"No successful submission found for run_id {run_id!r}. "
            "Submit first via: rcm-denial submit --run-id {run_id}"
        )

    latest = successful[0]
    confirmation_number = latest["confirmation_number"]
    payer_id = latest["payer_id"]

    adapter = get_submission_adapter(payer_id)
    logger.info(
        "Checking submission status",
        run_id=run_id,
        confirmation_number=confirmation_number,
        payer_id=payer_id,
    )
    return adapter.check_status(confirmation_number, payer_id)
