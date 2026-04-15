##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: submission.py
# Purpose: Pydantic models for Phase 5 payer submission.
#
##########################################################

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SubmissionResult(BaseModel):
    """
    Result returned by every BaseSubmissionAdapter.submit() call.
    Logged to submission_log table after each attempt.
    """
    success: bool
    submission_method: str = ""
    confirmation_number: str = ""    # payer-assigned confirmation / tracking ID
    response_code: str = ""          # HTTP status, EDI ACK code, or portal response code
    response_message: str = ""       # human-readable response text
    attempt_number: int = 1
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    response_received_at: Optional[datetime] = None
    error_detail: str = ""           # populated on failure


class SubmissionStatus(BaseModel):
    """
    Status of a previously submitted claim — returned by check_status().
    """
    confirmation_number: str
    payer_status: Literal[
        "received",       # payer confirmed receipt, not yet adjudicated
        "in_review",      # payer is actively reviewing
        "adjudicated",    # decision made (check paid_amount)
        "rejected",       # technical rejection (fix and resubmit)
        "unknown",        # status unavailable
    ] = "unknown"
    adjudication_date: Optional[datetime] = None
    paid_amount: Optional[float] = None
    denial_upheld: Optional[bool] = None
    payer_notes: str = ""
    checked_at: datetime = Field(default_factory=datetime.utcnow)
