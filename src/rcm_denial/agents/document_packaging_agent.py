##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: document_packaging_agent.py
# Purpose: LangGraph node that converts all workflow outputs
#          to individual PDFs, merges them into a final
#          submission package, writes metadata JSON and the
#          audit log, and populates the SubmissionPackage model.
#
##########################################################

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from rcm_denial.models.output import DenialWorkflowState, SubmissionPackage
from rcm_denial.services.audit_service import bind_claim_context, get_logger
from rcm_denial.services.pdf_service import (
    generate_analysis_report_pdf,
    generate_appeal_letter_pdf,
    generate_correction_plan_pdf,
    generate_cover_letter_pdf,
    merge_pdfs,
)

logger = get_logger(__name__)
NODE_NAME = "document_packaging_agent"


def _determine_package_type(state: DenialWorkflowState) -> str:
    if state.correction_plan and state.appeal_package:
        return "both"
    if state.correction_plan:
        return "resubmission"
    if state.appeal_package:
        return "appeal"
    if state.routing_decision == "write_off":
        return "write_off"
    return "failed"


def _write_metadata_json(state: DenialWorkflowState, output_dir: Path) -> Path:
    """Writes submission_metadata.json for the claim package."""
    claim = state.claim
    analysis = state.denial_analysis
    pkg_type = _determine_package_type(state)

    metadata = {
        "run_id": state.run_id,
        "batch_id": state.batch_id,
        "claim_id": claim.claim_id,
        "patient_id": claim.patient_id,
        "payer_id": claim.payer_id,
        "provider_id": claim.provider_id,
        "date_of_service": str(claim.date_of_service),
        "denial_date": str(claim.denial_date),
        "billed_amount": claim.billed_amount,
        "carc_code": claim.carc_code,
        "rarc_code": claim.rarc_code,
        "package_type": pkg_type,
        "recommended_action": state.routing_decision,
        "denial_category": analysis.denial_category if analysis else "unknown",
        "confidence_score": analysis.confidence_score if analysis else 0.0,
        "root_cause": analysis.root_cause if analysis else "Analysis not completed",
        "errors": state.errors,
        "processing_started_at": state.started_at.isoformat(),
        "packaged_at": datetime.utcnow().isoformat(),
        "appeal_deadline": (
            str(state.appeal_package.appeal_deadline)
            if state.appeal_package and state.appeal_package.appeal_deadline
            else None
        ),
        "days_until_appeal_deadline": (
            state.appeal_package.days_until_deadline
            if state.appeal_package
            else None
        ),
        "is_urgent": (
            state.appeal_package.is_urgent
            if state.appeal_package
            else False
        ),
    }

    metadata_path = output_dir / "submission_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    return metadata_path


def _write_audit_log(state: DenialWorkflowState, output_dir: Path) -> Path:
    """Writes the immutable audit log to audit_log.json."""
    audit_data = {
        "claim_id": state.claim.claim_id,
        "run_id": state.run_id,
        "batch_id": state.batch_id,
        "entries": [entry.model_dump() for entry in state.audit_log],
    }

    audit_path = output_dir / "audit_log.json"
    with open(audit_path, "w") as f:
        json.dump(audit_data, f, indent=2, default=str)

    return audit_path


def document_packaging_agent(state: DenialWorkflowState) -> DenialWorkflowState:
    """
    LangGraph node: Document Packaging Agent.

    Generates all PDFs, merges them into a final package,
    writes metadata.json and audit_log.json, and sets
    state.output_package.
    """
    start = time.perf_counter()
    claim = state.claim

    bind_claim_context(claim.claim_id, NODE_NAME, state.run_id)
    logger.info("Document packaging agent starting", claim_id=claim.claim_id)

    state.current_node = NODE_NAME
    state.add_audit(NODE_NAME, "started")

    try:
        from rcm_denial.config.settings import settings

        # Create per-claim output directory with two sub-folders
        output_dir = settings.output_dir / claim.claim_id
        package_dir = output_dir / "package"
        audit_dir = output_dir / "internal_audit"
        package_dir.mkdir(parents=True, exist_ok=True)
        audit_dir.mkdir(parents=True, exist_ok=True)

        generated_pdfs: list[Path] = []

        # 1. Cover letter (FIRST page of submission bundle)
        cover_pdf_path = package_dir / "00_cover_letter.pdf"
        try:
            generate_cover_letter_pdf(state, cover_pdf_path)
            generated_pdfs.append(cover_pdf_path)
        except Exception as exc:
            logger.warning("Cover letter PDF failed", error=str(exc))
            state.add_error(f"Cover letter PDF failed: {exc}")

        # 2. Analysis report PDF (always generated)
        analysis_pdf_path = package_dir / "01_denial_analysis.pdf"
        try:
            generate_analysis_report_pdf(state, analysis_pdf_path)
            generated_pdfs.append(analysis_pdf_path)
        except Exception as exc:
            logger.warning("Analysis report PDF failed", error=str(exc))
            state.add_error(f"Analysis PDF generation failed: {exc}")

        # 3. Correction plan PDF (if resubmission path)
        if state.correction_plan:
            correction_pdf_path = package_dir / "02_correction_plan.pdf"
            try:
                generate_correction_plan_pdf(state, correction_pdf_path)
                generated_pdfs.append(correction_pdf_path)
            except Exception as exc:
                logger.warning("Correction plan PDF failed", error=str(exc))
                state.add_error(f"Correction plan PDF failed: {exc}")

        # 4. Appeal letter PDF (if appeal path)
        if state.appeal_package:
            appeal_pdf_path = package_dir / "03_appeal_letter.pdf"
            try:
                generate_appeal_letter_pdf(state, appeal_pdf_path)
                generated_pdfs.append(appeal_pdf_path)
            except Exception as exc:
                logger.warning("Appeal letter PDF failed", error=str(exc))
                state.add_error(f"Appeal letter PDF failed: {exc}")

        # 5. Merge into final submission package
        final_pdf_path = None
        if generated_pdfs:
            final_pdf_path = package_dir / f"SUBMISSION_PACKAGE_{claim.claim_id}.pdf"
            try:
                merge_pdfs(generated_pdfs, final_pdf_path)
            except Exception as exc:
                logger.warning("PDF merge failed — individual PDFs preserved", error=str(exc))
                final_pdf_path = generated_pdfs[0] if generated_pdfs else None

        # 6. Write internal audit data (NOT submitted to payer)
        metadata_path = _write_metadata_json(state, audit_dir)
        audit_path = _write_audit_log(state, audit_dir)

        # 7. Build SubmissionPackage
        duration_ms = (time.perf_counter() - start) * 1000
        pkg_type = _determine_package_type(state)
        status = "complete" if not state.errors else ("partial" if generated_pdfs else "failed")

        total_duration = (datetime.utcnow() - state.started_at).total_seconds() * 1000

        output_package = SubmissionPackage(
            claim_id=claim.claim_id,
            run_id=state.run_id,
            output_dir=str(output_dir),
            pdf_package_path=str(final_pdf_path) if final_pdf_path else None,
            metadata_json_path=str(metadata_path),
            audit_log_path=str(audit_path),
            package_type=pkg_type,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            processing_duration_ms=total_duration,
            summary=(
                f"Claim {claim.claim_id}: {pkg_type.upper()} package generated. "
                f"Action: {state.routing_decision}. "
                f"PDFs: {len(generated_pdfs)}. "
                f"Errors: {len(state.errors)}."
            ),
        )

        state.output_package = output_package
        state.is_complete = True

        state.add_audit(
            NODE_NAME, "completed",
            details=f"Package: {pkg_type}, Status: {status}, PDFs: {len(generated_pdfs)}",
            duration_ms=duration_ms,
        )

        # Re-write audit log now that packaging entry is included
        _write_audit_log(state, output_dir)

        logger.info(
            "Document packaging complete",
            claim_id=claim.claim_id,
            package_type=pkg_type,
            status=status,
            output_dir=str(output_dir),
            pdf_count=len(generated_pdfs),
            total_duration_ms=round(total_duration, 2),
        )

    except Exception as exc:
        state.add_error(f"Document packaging agent failed: {exc}")
        state.add_audit(NODE_NAME, "failed", details=str(exc))
        state.is_complete = True
        logger.error("Document packaging agent failed", claim_id=claim.claim_id, error=str(exc))

    return state
