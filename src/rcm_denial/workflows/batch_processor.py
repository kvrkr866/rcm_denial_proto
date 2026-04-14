##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: batch_processor.py
# Purpose: Reads a CSV of denied claims via claim_intake.stream_claims()
#          and processes each through the LangGraph workflow one at a time.
#          Urgent claims (HIGH priority or deadline <= 7 days) are
#          processed first. Produces a BatchReport with per-claim results
#          and intake validation summary.
#
##########################################################

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from rcm_denial.models.output import BatchReport, ClaimResult
from rcm_denial.services.audit_service import get_logger
from rcm_denial.services.claim_intake import get_intake_report, stream_claims
from rcm_denial.workflows.denial_graph import process_claim

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Idempotency check
# ------------------------------------------------------------------ #

def _is_already_processed(claim_id: str, output_dir: Path) -> bool:
    """
    Returns True if a completed submission package already exists for this claim.
    Allows safe re-running of batches without reprocessing finished claims.
    """
    metadata = output_dir / claim_id / "submission_metadata.json"
    if metadata.exists():
        try:
            with open(metadata) as f:
                meta = json.load(f)
            return meta.get("package_type") not in ("failed", None)
        except Exception:
            return False
    return False


# ------------------------------------------------------------------ #
# Main batch processor
# ------------------------------------------------------------------ #

def process_batch(
    csv_path: str | Path,
    batch_id: str = "",
    skip_completed: bool = True,
    source: str = "claim_db",
    on_error: Literal["proceed", "stop"] = "proceed",
) -> BatchReport:
    """
    Processes all denied claims in a CSV file.

    Flow:
        1. stream_claims() reads CSV, maps fields, validates each row,
           records to DB, yields ClaimRecord objects (urgent first).
        2. Each valid claim is optionally skipped (idempotency check).
        3. Each non-skipped claim is processed through the full pipeline.
        4. BatchReport is finalized with intake stats from DB.

    Args:
        csv_path:       Path to the input CSV file.
        batch_id:       Optional batch identifier. Auto-generated if empty.
        skip_completed: If True, skip claims with existing output packages.
        source:         Field map key — matches external CSV column names to
                        internal names. Default 'claim_db'. Add new sources
                        to FIELD_MAPS in claim_intake.py for other systems.
        on_error:       'proceed' — log bad rows to DB, continue (default).
                        'stop'    — raise ClaimValidationError on first bad row.

    Returns:
        BatchReport with counts, per-claim results, and intake stats.
    """
    from rcm_denial.config.settings import settings

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    # Auto-generate batch_id if not provided
    if not batch_id:
        batch_id = hashlib.sha256(
            f"{csv_path.name}:{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:12]

    logger.info(
        "Batch processing starting",
        csv_path=str(csv_path),
        batch_id=batch_id,
        source=source,
        on_error=on_error,
    )

    report = BatchReport(
        batch_id=batch_id,
        input_csv=str(csv_path),
        source_mapping=source,
        on_error_mode=on_error,
        started_at=datetime.utcnow(),
    )

    batch_start = time.perf_counter()

    # ---------------------------------------------------------------- #
    # Main loop — stream_claims yields valid ClaimRecords, urgent first
    # ---------------------------------------------------------------- #

    for claim in stream_claims(
        csv_path=csv_path,
        source=source,
        on_error=on_error,
        batch_id=batch_id,
    ):
        # ---- Idempotency: skip already-completed claims ----
        if skip_completed and _is_already_processed(claim.claim_id, settings.output_dir):
            logger.info("Skipping already-processed claim", claim_id=claim.claim_id)
            report.skipped += 1
            report.claim_results.append(ClaimResult(
                claim_id=claim.claim_id,
                status="skipped",
                package_type="skipped",
            ))
            continue

        # ---- Process through pipeline ----
        claim_start = time.perf_counter()

        logger.info(
            "Processing claim",
            claim_id=claim.claim_id,
            payer=claim.payer_name or claim.payer_id,
            carc=claim.carc_code,
            priority=claim.priority_label,
            days_to_deadline=claim.days_to_deadline,
            batch_id=batch_id,
        )

        try:
            package = process_claim(claim, batch_id=batch_id)
            duration_ms = (time.perf_counter() - claim_start) * 1000

            if package.status == "complete":
                report.completed += 1
            elif package.status == "partial":
                report.partial += 1
            else:
                report.failed += 1

            report.claim_results.append(ClaimResult(
                claim_id=claim.claim_id,
                status=package.status,        # type: ignore[arg-type]
                package_type=package.package_type,
                output_dir=package.output_dir,
                errors=[],
                duration_ms=duration_ms,
            ))

            logger.info(
                "Claim processed",
                claim_id=claim.claim_id,
                status=package.status,
                package_type=package.package_type,
                duration_ms=round(duration_ms, 2),
            )

        except Exception as exc:
            # Never abort the batch on a single claim failure
            duration_ms = (time.perf_counter() - claim_start) * 1000
            report.failed += 1
            report.claim_results.append(ClaimResult(
                claim_id=claim.claim_id,
                status="failed",
                errors=[str(exc)],
                duration_ms=duration_ms,
            ))
            logger.error(
                "Claim processing failed — continuing batch",
                claim_id=claim.claim_id,
                error=str(exc),
            )

    # ---------------------------------------------------------------- #
    # Finalize report — pull intake stats from DB
    # ---------------------------------------------------------------- #

    intake = get_intake_report(batch_id=batch_id)
    report.total_claims = intake["total"]      # total CSV rows (valid + rejected)
    report.rejected = intake["rejected"]       # rows that failed validation

    report.completed_at = datetime.utcnow()
    report.total_duration_ms = (time.perf_counter() - batch_start) * 1000

    # Write batch summary JSON
    summary_path = settings.output_dir / f"batch_summary_{batch_id}.json"
    with open(summary_path, "w") as f:
        json.dump(report.model_dump(), f, indent=2, default=str)

    logger.info(
        "Batch processing complete",
        batch_id=batch_id,
        total=report.total_claims,
        completed=report.completed,
        partial=report.partial,
        failed=report.failed,
        skipped=report.skipped,
        rejected=report.rejected,
        success_rate=f"{report.success_rate}%",
        total_duration_ms=round(report.total_duration_ms, 2),
        summary_path=str(summary_path),
    )

    return report


# ------------------------------------------------------------------ #
# Future: async parallel processing scaffold (not active)
# ------------------------------------------------------------------ #

async def _process_batch_async(
    claims: list,
    batch_id: str,
    max_concurrent: int = 4,
) -> list[ClaimResult]:
    """
    FUTURE IMPLEMENTATION — not active yet.
    Replace process_batch() loop with this for concurrent processing.
    """
    import asyncio

    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[ClaimResult] = []

    async def _process_one(claim) -> ClaimResult:
        async with semaphore:
            loop = asyncio.get_event_loop()
            start = time.perf_counter()
            try:
                package = await loop.run_in_executor(None, process_claim, claim, batch_id)
                duration_ms = (time.perf_counter() - start) * 1000
                return ClaimResult(
                    claim_id=claim.claim_id,
                    status=package.status,          # type: ignore[arg-type]
                    package_type=package.package_type,
                    output_dir=package.output_dir,
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                duration_ms = (time.perf_counter() - start) * 1000
                return ClaimResult(
                    claim_id=claim.claim_id,
                    status="failed",
                    errors=[str(exc)],
                    duration_ms=duration_ms,
                )

    tasks = [_process_one(c) for c in claims]
    results = await asyncio.gather(*tasks)
    return list(results)
