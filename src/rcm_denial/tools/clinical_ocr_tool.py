##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: clinical_ocr_tool.py
# Purpose: Gap 16 — OCR extraction for clinical PDFs.
#
#          Extracts structured content from lab reports, imaging
#          studies, pathology reports, and other clinical documents
#          returned by Stage 2 EHR fetch (targeted_ehr_agent).
#
#          Reuses the same pytesseract / pdf2image infrastructure as
#          eob_ocr_tool.py.  Swap extract_text_from_pdf() for AWS
#          Textract or Azure Form Recognizer without other changes.
#
#          Output: DiagnosticReport (models/claim.py)
#
##########################################################

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from rcm_denial.models.claim import DiagnosticReport
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Regex patterns for clinical document fields
# ------------------------------------------------------------------ #

# Report date patterns: "Date: 01/15/2024", "Collected: 2024-01-15"
_DATE_PATTERN = re.compile(
    r"(?:date|collected|reported|resulted|performed)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

# Impression / conclusion section
_IMPRESSION_PATTERN = re.compile(
    r"(?:impression|conclusion|summary|interpretation|finding)[s]?[:\s]+(.*?)(?=\n[A-Z]|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# Abnormal flag markers in lab results
_ABNORMAL_PATTERN = re.compile(
    r"([A-Z][A-Za-z\s]+?)\s+[\d.]+\s+(?:H|L|HH|LL|CRIT|PANIC|HIGH|LOW|ABNORMAL)",
    re.IGNORECASE,
)

# Modality detection for imaging
_MODALITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("MRI",    re.compile(r"\bMRI\b|\bmagnetic resonance\b", re.IGNORECASE)),
    ("CT",     re.compile(r"\bCT\b|\bcomputed tomography\b|\bCAT scan\b", re.IGNORECASE)),
    ("X-RAY",  re.compile(r"\bx-?ray\b|\bradiograph\b", re.IGNORECASE)),
    ("US",     re.compile(r"\bultrasound\b|\bsonograph\b|\bUS\b", re.IGNORECASE)),
    ("PET",    re.compile(r"\bPET\b|\bpositron emission\b", re.IGNORECASE)),
    ("NM",     re.compile(r"\bnuclear medicine\b|\bscintigraphy\b", re.IGNORECASE)),
    ("ECHO",   re.compile(r"\bechocardiograph\b|\bechocardiogram\b", re.IGNORECASE)),
]

# Document category keywords
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "lab":       ["laboratory", "lab result", "cbc", "panel", "culture", "hba1c", "lipid", "urinalysis"],
    "imaging":   ["radiology", "imaging", "mri", "ct scan", "x-ray", "ultrasound", "pet scan"],
    "pathology": ["pathology", "biopsy", "histology", "cytology", "specimen"],
    "cardiology":["ecg", "ekg", "echocardiogram", "stress test", "holter", "cardiac catheterization"],
}


# ------------------------------------------------------------------ #
# PDF text extraction (same pattern as eob_ocr_tool)
# ------------------------------------------------------------------ #

def _extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Extracts raw text from a clinical PDF using pdf2image + pytesseract.
    Raises ImportError if OCR dependencies not installed.
    Raises FileNotFoundError if the PDF path does not exist.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"Clinical PDF not found: {pdf_path}")

    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise ImportError(
            "OCR dependencies missing. Install: pip install pytesseract pdf2image"
        ) from exc

    pages = convert_from_path(str(pdf_path), dpi=300)
    text_parts = [pytesseract.image_to_string(page) for page in pages]
    return "\n".join(text_parts)


# ------------------------------------------------------------------ #
# Category and modality detection
# ------------------------------------------------------------------ #

def _detect_category(text: str) -> str:
    """Infers document category from text content."""
    text_lower = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "other"


def _detect_modality(text: str) -> Optional[str]:
    """Extracts imaging modality from report text (None for non-imaging)."""
    for modality, pattern in _MODALITY_PATTERNS:
        if pattern.search(text):
            return modality
    return None


def _parse_report_date(text: str) -> Optional[date]:
    """Extracts the first date found near a date label."""
    match = _DATE_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _extract_impression(text: str) -> str:
    """Extracts the impression / conclusion section."""
    match = _IMPRESSION_PATTERN.search(text)
    if not match:
        return ""
    raw = match.group(1).strip()
    # Limit to first 1000 characters to keep LLM context manageable
    return raw[:1000]


def _extract_abnormal_flags(text: str) -> list[str]:
    """Returns a list of abnormal lab value descriptions."""
    flags = []
    for match in _ABNORMAL_PATTERN.finditer(text):
        flag = match.group(0).strip()
        if flag not in flags:
            flags.append(flag)
    return flags[:10]  # cap at 10 flags


def _extract_report_name(text: str, category: str) -> str:
    """Heuristically extracts the report title from the first few lines."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # Look for an all-caps or title-case line in the first 10 lines
    for line in lines[:10]:
        if len(line) > 5 and (line.isupper() or line.istitle()):
            return line[:100]
    # Fallback: category label
    return f"{category.capitalize()} Report"


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def extract_clinical_report(
    pdf_path: str | Path,
    claim_id: str = "",
    fetch_description: str = "",
) -> DiagnosticReport:
    """
    Extract a DiagnosticReport from a clinical PDF file.

    Args:
        pdf_path:          Path to the clinical report PDF.
        claim_id:          Associated claim ID (used as report_id prefix).
        fetch_description: Hint from evidence_check_agent (e.g. "MRI brain").

    Returns:
        DiagnosticReport with structured fields populated.
        Falls back to a minimal report with raw text if parsing fails.
    """
    pdf_path = Path(pdf_path)
    logger.info("Extracting clinical report", path=str(pdf_path), claim_id=claim_id)

    try:
        raw_text = _extract_text_from_pdf(pdf_path)
    except FileNotFoundError:
        logger.warning("Clinical PDF not found", path=str(pdf_path))
        return DiagnosticReport(
            report_id=f"{claim_id}-MISSING",
            report_category="other",
            report_name="Report Not Found",
            conclusion=f"PDF not found at: {pdf_path}",
            is_available=False,
            source="ocr_failed",
        )
    except ImportError as exc:
        logger.warning("OCR unavailable for clinical report", error=str(exc))
        return DiagnosticReport(
            report_id=f"{claim_id}-NO-OCR",
            report_category="other",
            report_name=f"Clinical Report ({pdf_path.name})",
            conclusion="OCR dependencies not installed — manual review required",
            is_available=False,
            source="ocr_unavailable",
        )
    except Exception as exc:
        logger.error("Clinical OCR extraction failed", path=str(pdf_path), error=str(exc))
        return DiagnosticReport(
            report_id=f"{claim_id}-ERROR",
            report_category="other",
            report_name=f"Clinical Report ({pdf_path.name})",
            conclusion=f"Extraction error: {exc}",
            is_available=False,
            source="ocr_error",
        )

    # ---- Parse structured fields from raw text ----
    category = _detect_category(raw_text)
    modality  = _detect_modality(raw_text) if category == "imaging" else None
    rpt_date  = _parse_report_date(raw_text)
    impression = _extract_impression(raw_text)
    abnormals  = _extract_abnormal_flags(raw_text)
    rpt_name   = _extract_report_name(raw_text, category)

    # Key findings: impression split into sentences
    key_findings: list[str] = []
    if impression:
        sentences = [s.strip() for s in re.split(r"[.\n]", impression) if s.strip()]
        key_findings = sentences[:5]

    logger.info(
        "Clinical report extracted",
        claim_id=claim_id,
        category=category,
        report_name=rpt_name,
        has_impression=bool(impression),
        abnormal_count=len(abnormals),
    )

    return DiagnosticReport(
        report_id=f"{claim_id}-{pdf_path.stem}",
        report_category=category,
        report_name=rpt_name,
        report_date=rpt_date,
        conclusion=impression or f"See full report: {pdf_path.name}",
        key_findings=key_findings,
        abnormal_flags=abnormals,
        modality=modality,
        is_available=True,
        source="ocr",
        content_summary=raw_text[:2000],  # first 2000 chars for LLM context
    )


def extract_clinical_reports_from_paths(
    pdf_paths: list[str | Path],
    claim_id: str = "",
    fetch_description: str = "",
) -> list[DiagnosticReport]:
    """
    Batch version: extract DiagnosticReport from multiple PDFs.
    Used when the Stage 2 EHR fetch returns several report files.
    """
    reports = []
    for path in pdf_paths:
        report = extract_clinical_report(path, claim_id=claim_id, fetch_description=fetch_description)
        reports.append(report)
    return reports
