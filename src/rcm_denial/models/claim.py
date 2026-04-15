##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: claim.py
# Purpose: Pydantic models for ClaimRecord (input) and
#          EnrichedData (post-enrichment) used throughout
#          the LangGraph workflow state.
#
##########################################################

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ------------------------------------------------------------------ #
# Input claim record (pipeline-internal model)
# ------------------------------------------------------------------ #

class ClaimRecord(BaseModel):
    """
    Represents a single denied claim flowing through the pipeline.

    Populated by claim_intake.stream_claims() from the input CSV.
    All required fields are guaranteed present after intake validation.

    denial_reason is intentionally None at input time — it is populated
    later by the enrichment agent via EOB OCR (eob_ocr_tool).
    """

    # ---------------------------------------------------------------- #
    # Core required fields
    # ---------------------------------------------------------------- #
    claim_id: str = Field(..., description="Unique claim identifier")
    patient_id: str = Field(..., description="Patient/member ID")
    payer_id: str = Field(..., description="Payer identifier")
    provider_id: str = Field(..., description="Provider NPI or internal ID")
    date_of_service: date
    cpt_codes: list[str] = Field(..., description="CPT/HCPCS procedure codes")
    diagnosis_codes: list[str] = Field(..., description="ICD-10 diagnosis codes")
    carc_code: str = Field(..., description="Claim Adjustment Reason Code (normalized, no prefix)")
    denial_date: date
    billed_amount: float = Field(..., gt=0, description="Total billed amount in USD")

    # ---------------------------------------------------------------- #
    # Denial detail — populated by EOB OCR, not from CSV
    # ---------------------------------------------------------------- #
    denial_reason: Optional[str] = Field(
        None,
        description="Free-text denial reason — extracted from EOB PDF by enrichment agent",
    )
    rarc_code: Optional[str] = Field(None, description="Remittance Advice Remark Code")
    eob_pdf_path: Optional[str] = Field(None, description="Path to the EOB PDF file")

    # ---------------------------------------------------------------- #
    # Patient
    # ---------------------------------------------------------------- #
    patient_name: Optional[str] = None
    patient_dob: Optional[date] = None
    member_id: Optional[str] = Field(None, description="Insurance member ID (same as patient_id)")

    # ---------------------------------------------------------------- #
    # Service / clinical
    # ---------------------------------------------------------------- #
    invoice_number: Optional[str] = None
    status: Optional[str] = Field(None, description="Claim status: Denied / Submitted / etc.")
    cpt_description: Optional[str] = Field(None, description="Human-readable CPT code description")
    specialty: Optional[str] = None
    facility_type: Optional[str] = None
    requires_auth: Optional[bool] = Field(None, description="Whether prior auth is required")

    # ---------------------------------------------------------------- #
    # Provider
    # ---------------------------------------------------------------- #
    provider_npi: Optional[str] = Field(None, description="Rendering provider NPI")
    rendering_provider: Optional[str] = Field(None, description="Rendering provider full name")

    # ---------------------------------------------------------------- #
    # Payer contact & submission
    # ---------------------------------------------------------------- #
    payer_name: Optional[str] = Field(None, description="Full payer name")
    payer_phone: Optional[str] = None
    payer_portal_url: Optional[str] = None
    payer_response_time_days: Optional[int] = None
    ivr_style: Optional[str] = Field(None, description="IVR type: standard / complex / automated")
    primary_channel: Optional[str] = Field(None, description="Preferred submission channel: voice / portal / fax")
    payer_filing_deadline_days: Optional[int] = Field(None, description="Payer timely filing window in days")

    # ---------------------------------------------------------------- #
    # Financial
    # ---------------------------------------------------------------- #
    contracted_rate: Optional[float] = Field(None, description="Contracted reimbursement rate")
    paid_amount: Optional[float] = Field(None, description="Amount actually paid by payer")

    # ---------------------------------------------------------------- #
    # AR / workflow tracking
    # ---------------------------------------------------------------- #
    days_in_ar: Optional[int] = Field(None, description="Days claim has been in accounts receivable")
    prior_appeal_attempts: int = Field(0, description="Number of prior appeal attempts")
    appealable: bool = Field(True, description="Whether the claim can be appealed")
    rebillable: bool = Field(True, description="Whether the claim can be resubmitted")
    appeal_deadline: Optional[date] = Field(None, description="Hard deadline for filing an appeal")
    days_to_deadline: Optional[int] = Field(None, description="Days remaining until appeal deadline")

    # ---------------------------------------------------------------- #
    # Priority scoring
    # ---------------------------------------------------------------- #
    appeal_win_probability: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Estimated probability of appeal success (0.0 – 1.0)",
    )
    priority_score: Optional[float] = Field(None, description="Composite priority score")
    priority_label: Optional[str] = Field(None, description="Priority tier: HIGH / MEDIUM / LOW")

    # ---------------------------------------------------------------- #
    # Extra fields catchall (for forward compatibility)
    # ---------------------------------------------------------------- #
    extra_fields: dict[str, Any] = Field(default_factory=dict)

    # ---------------------------------------------------------------- #
    # Validators
    # ---------------------------------------------------------------- #

    @field_validator("cpt_codes", "diagnosis_codes", mode="before")
    @classmethod
    def parse_comma_separated(cls, v: str | list) -> list[str]:
        if isinstance(v, list):
            return [str(c).strip() for c in v if str(c).strip()]
        if isinstance(v, str):
            return [code.strip() for code in v.split(",") if code.strip()]
        return []

    @field_validator("billed_amount", mode="before")
    @classmethod
    def parse_amount(cls, v: str | float) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            return float(v.replace("$", "").replace(",", "").strip())
        return v

    @field_validator("carc_code", mode="before")
    @classmethod
    def normalize_carc(cls, v: str) -> str:
        """Normalizes CARC code: strips group prefixes and uppercases.
        'CO-252' → '252', 'PR-4' → '4', '97' → '97'
        """
        code = str(v).strip().upper()
        for prefix in ("CO-", "PR-", "OA-", "PI-", "CR-"):
            if code.startswith(prefix):
                return code[len(prefix):]
        return code

    # ---------------------------------------------------------------- #
    # Properties
    # ---------------------------------------------------------------- #

    @property
    def eob_path(self) -> Optional[Path]:
        return Path(self.eob_pdf_path) if self.eob_pdf_path else None

    @property
    def is_appeal_urgent(self) -> bool:
        """True if appeal deadline is within 30 days or past."""
        if self.days_to_deadline is not None:
            return self.days_to_deadline <= 30
        return False

    model_config = {"extra": "allow"}


# ------------------------------------------------------------------ #
# Patient data (from patient data service)
# ------------------------------------------------------------------ #

class InsuranceCoverage(BaseModel):
    plan_name: str
    plan_id: str
    group_number: Optional[str] = None
    member_id: str
    effective_date: Optional[date] = None
    termination_date: Optional[date] = None
    copay: Optional[float] = None
    deductible: Optional[float] = None
    is_active: bool = True


class PatientData(BaseModel):
    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    insurance_coverage: list[InsuranceCoverage] = Field(default_factory=list)
    is_eligible: bool = True
    eligibility_notes: Optional[str] = None
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------------ #
# Payer policy data
# ------------------------------------------------------------------ #

class PayerPolicy(BaseModel):
    payer_id: str
    payer_name: str
    billing_guidelines: list[str] = Field(default_factory=list)
    covered_cpt_codes: list[str] = Field(default_factory=list)
    prior_auth_required_codes: list[str] = Field(default_factory=list)
    timely_filing_limit_days: int = 365
    appeal_deadline_days: int = 180
    appeal_address: Optional[str] = None
    appeal_fax: Optional[str] = None
    appeal_portal_url: Optional[str] = None
    contract_rate: Optional[float] = None
    notes: list[str] = Field(default_factory=list)
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------------ #
# EHR / clinical documentation
# ------------------------------------------------------------------ #

class EhrDocument(BaseModel):
    document_type: str  # e.g. "encounter_note", "procedure_record", "auth_record"
    content_summary: str
    document_date: Optional[date] = None
    author: Optional[str] = None
    is_available: bool = True


# Gap 8 — DiagnosticReport: lab results, imaging studies, pathology reports
# Maps to FHIR DiagnosticReport / Observation / ImagingStudy resources.
class DiagnosticReport(BaseModel):
    """
    A single diagnostic report (lab, imaging, pathology, or cardiology).

    FHIR mapping:
      DiagnosticReport.category → report_category
      DiagnosticReport.code.text → report_name
      DiagnosticReport.effectiveDateTime → report_date
      DiagnosticReport.conclusion → conclusion
      DiagnosticReport.result (Observation refs) → key_findings
      ImagingStudy.series[].modality → modality
    """
    report_id: str = ""
    report_category: str  # "lab" | "imaging" | "pathology" | "cardiology" | "other"
    report_name: str      # e.g. "CBC with Differential", "MRI Brain w/o contrast"
    report_date: Optional[date] = None
    ordering_provider: Optional[str] = None
    performing_lab_or_facility: Optional[str] = None
    conclusion: str = ""              # narrative summary / impression
    key_findings: list[str] = Field(default_factory=list)   # bullet list of significant results
    abnormal_flags: list[str] = Field(default_factory=list) # flagged abnormal values
    modality: Optional[str] = None    # imaging only: "MRI" | "CT" | "X-RAY" | "US" | etc.
    is_available: bool = True
    source: str = "EHR"               # "EHR" | "OCR" | "manual"
    content_summary: str = ""         # full text snippet for RAG / LLM context


class EhrData(BaseModel):
    patient_id: str
    provider_id: str
    encounter_notes: list[EhrDocument] = Field(default_factory=list)
    procedure_details: list[EhrDocument] = Field(default_factory=list)
    prior_auth_records: list[EhrDocument] = Field(default_factory=list)
    diagnosis_justifications: list[EhrDocument] = Field(default_factory=list)
    # Gap 12 — Stage 2 targeted fetch: lab / imaging / pathology reports
    diagnostic_reports: list[DiagnosticReport] = Field(default_factory=list)
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
    # Tracks whether Stage 2 targeted fetch has been performed
    stage2_fetched: bool = False

    @property
    def has_auth_documentation(self) -> bool:
        return any(d.is_available for d in self.prior_auth_records)

    @property
    def has_encounter_notes(self) -> bool:
        return any(d.is_available for d in self.encounter_notes)

    @property
    def has_diagnostic_reports(self) -> bool:
        return any(r.is_available for r in self.diagnostic_reports)


# ------------------------------------------------------------------ #
# EOB OCR extraction result
# ------------------------------------------------------------------ #

class EobExtractedData(BaseModel):
    claim_id: Optional[str] = None
    carc_codes_found: list[str] = Field(default_factory=list)
    rarc_codes_found: list[str] = Field(default_factory=list)
    denial_remarks: list[str] = Field(default_factory=list)
    billed_amount: Optional[float] = None
    allowed_amount: Optional[float] = None
    adjustment_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    raw_text_excerpt: str = ""
    ocr_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_method: str = "pytesseract"
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------------ #
# SOP / Knowledge base retrieval result
# ------------------------------------------------------------------ #

class SopResult(BaseModel):
    source: str
    title: str
    content_snippet: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    carc_applicability: list[str] = Field(default_factory=list)
    payer_applicability: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# Aggregated enriched data (output of enrichment agent)
# ------------------------------------------------------------------ #

class EnrichedData(BaseModel):
    patient_data: Optional[PatientData] = None
    payer_policy: Optional[PayerPolicy] = None
    ehr_data: Optional[EhrData] = None
    eob_data: Optional[EobExtractedData] = None
    sop_results: list[SopResult] = Field(default_factory=list)
    enrichment_errors: list[str] = Field(default_factory=list)
    enriched_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_fully_enriched(self) -> bool:
        return all([
            self.patient_data is not None,
            self.payer_policy is not None,
            self.ehr_data is not None,
        ])
