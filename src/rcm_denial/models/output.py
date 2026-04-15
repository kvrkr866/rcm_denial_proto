##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: output.py
# Purpose: Pydantic models for the LangGraph workflow state
#          (DenialWorkflowState), SubmissionPackage (final output),
#          AuditEntry (immutable audit log), and BatchReport.
#
##########################################################

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .analysis import CorrectionPlan, DenialAnalysis, EvidenceCheckResult
from .appeal import AppealPackage
from .claim import ClaimRecord, EnrichedData


# ------------------------------------------------------------------ #
# Audit log entry
# ------------------------------------------------------------------ #

class AuditEntry(BaseModel):
    """Immutable record of a single step in the processing pipeline."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    node_name: str
    claim_id: str
    status: Literal["started", "completed", "failed", "skipped"]
    details: str = ""
    duration_ms: Optional[float] = None
    token_usage: Optional[dict[str, int]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------ #
# Final submission package
# ------------------------------------------------------------------ #

class SubmissionPackage(BaseModel):
    """Final output artifact for a single processed claim."""

    claim_id: str
    run_id: str
    output_dir: str
    pdf_package_path: Optional[str] = None
    metadata_json_path: Optional[str] = None
    audit_log_path: Optional[str] = None
    package_type: Literal["resubmission", "appeal", "both", "write_off", "failed"]
    status: Literal["complete", "partial", "failed"]
    processing_duration_ms: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    summary: str = ""


# ------------------------------------------------------------------ #
# LangGraph shared workflow state
# ------------------------------------------------------------------ #

class DenialWorkflowState(BaseModel):
    """
    Shared state object passed between all LangGraph nodes.
    Every agent reads from and writes to this state.
    Immutable fields (claim, run_id) are set at initialization.
    """

    # ---- Immutable inputs ----
    claim: ClaimRecord
    run_id: str = Field(default="")
    batch_id: str = Field(default="")
    started_at: datetime = Field(default_factory=datetime.utcnow)

    # ---- Agent outputs (populated as graph progresses) ----
    enriched_data: Optional[EnrichedData] = None
    denial_analysis: Optional[DenialAnalysis] = None
    evidence_check: Optional[EvidenceCheckResult] = None   # LLM call 1 output
    correction_plan: Optional[CorrectionPlan] = None
    appeal_package: Optional[AppealPackage] = None
    output_package: Optional[SubmissionPackage] = None

    # ---- Human-in-the-loop (Phase 4) ----
    human_feedback: Optional[str] = None
    human_review_status: Literal["pending", "approved", "re_routed", "human_override", "written_off", "submitted", ""] = ""
    # Reviewer guidance injected into re-run LLM prompts
    human_notes: str = ""
    # True when response package was written by a human (not LLM)
    is_human_override: bool = False
    # How many HITL review cycles this claim has gone through
    review_count: int = 0

    # ---- Control flow ----
    routing_decision: Literal["resubmit", "appeal", "both", "write_off", ""] = ""
    current_node: str = "intake"
    is_complete: bool = False

    # ---- Error tracking ----
    errors: list[str] = Field(default_factory=list)

    # ---- Audit trail ----
    audit_log: list[AuditEntry] = Field(default_factory=list)

    def add_audit(
        self,
        node_name: str,
        status: Literal["started", "completed", "failed", "skipped"],
        details: str = "",
        duration_ms: Optional[float] = None,
        token_usage: Optional[dict[str, int]] = None,
    ) -> None:
        self.audit_log.append(AuditEntry(
            node_name=node_name,
            claim_id=self.claim.claim_id,
            status=status,
            details=details,
            duration_ms=duration_ms,
            token_usage=token_usage,
        ))

    def add_error(self, error: str) -> None:
        self.errors.append(f"[{self.current_node}] {error}")

    @classmethod
    def create(cls, claim: ClaimRecord, batch_id: str = "") -> "DenialWorkflowState":
        """Factory: creates a new state with a deterministic run_id."""
        run_id = hashlib.sha256(
            f"{claim.claim_id}:{batch_id}".encode()
        ).hexdigest()[:16]
        return cls(claim=claim, run_id=run_id, batch_id=batch_id)


# ------------------------------------------------------------------ #
# Batch processing report
# ------------------------------------------------------------------ #

class ClaimResult(BaseModel):
    claim_id: str
    status: Literal["complete", "partial", "failed", "skipped"]
    package_type: str = ""
    output_dir: Optional[str] = None
    errors: list[str] = Field(default_factory=list)
    duration_ms: Optional[float] = None
    processed_at: datetime = Field(default_factory=datetime.utcnow)


class BatchReport(BaseModel):
    """Summary report generated after processing a full batch CSV."""

    batch_id: str
    input_csv: str
    source_mapping: str = "claim_db"        # FIELD_MAPS key used for this batch
    on_error_mode: str = "proceed"           # 'proceed' or 'stop'

    # ---- Counts ----
    total_claims: int = 0                    # total rows in CSV (valid + rejected)
    completed: int = 0                       # pipeline status = complete
    partial: int = 0                         # pipeline status = partial
    failed: int = 0                          # pipeline status = failed
    skipped: int = 0                         # already processed (idempotency)
    rejected: int = 0                        # failed intake validation, never entered pipeline

    # ---- Per-claim results (pipeline-processed claims only) ----
    claim_results: list[ClaimResult] = Field(default_factory=list)

    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    total_duration_ms: Optional[float] = None

    @property
    def processed_count(self) -> int:
        """Claims that entered the pipeline (excludes rejected + skipped)."""
        return self.completed + self.partial + self.failed

    @property
    def success_rate(self) -> float:
        """Completed / processed (excludes rejected and skipped from denominator)."""
        if self.processed_count == 0:
            return 0.0
        return round((self.completed / self.processed_count) * 100, 2)
