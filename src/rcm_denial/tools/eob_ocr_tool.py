##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: eob_ocr_tool.py
# Purpose: Extracts CARC codes, RARC codes, denial remarks,
#          and monetary amounts from EOB PDF files using
#          pytesseract + pdf2image OCR. Architecture supports
#          swap to AWS Textract by replacing extract_text_from_pdf().
#
##########################################################

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from rcm_denial.models.claim import DenialCodeDetail, EobExtractedData
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# GLOSSARY parser — extracts structured denial codes from EOB bottom
# ------------------------------------------------------------------ #

def _parse_glossary(raw_text: str) -> DenialCodeDetail | None:
    """
    Parse the GLOSSARY section at the bottom of an EOB PDF.

    Format:
        GLOSSARY: GROUP, REASON, MOA, REMARK AND REASON CODES
        CO-252    An attachment/other documentation is required...
        M127      Missing patient medical record for this service.

    Returns DenialCodeDetail with major (CARC) and minor (RARC) codes
    plus their full descriptions, a natural summary, and artifact source.
    """
    # Find GLOSSARY section
    glossary_marker = "GLOSSARY:"
    idx = raw_text.upper().find(glossary_marker.upper())
    if idx < 0:
        return None

    glossary_text = raw_text[idx:]

    # Pattern: code at start of line followed by description
    # CARC codes: CO-nnn, PR-nnn, OA-nnn, PI-nnn, CR-nnn
    # RARC codes: Mnnn, NNnn, MAnnn
    code_pattern = re.compile(
        r'^((?:CO|PR|OA|PI|CR)-\d{1,3}|[NM]A?\d{2,3})\s*\n((?:(?!^(?:CO|PR|OA|PI|CR)-\d|^[NM]A?\d).+\n?)*)',
        re.MULTILINE,
    )

    matches = code_pattern.findall(glossary_text)

    if not matches:
        # Fallback: try simpler pattern
        code_pattern2 = re.compile(
            r'((?:CO|PR|OA|PI|CR)-\d{1,3})\s+(.*?)(?=(?:CO|PR|OA|PI|CR)-\d|[NM]A?\d|\Z)',
            re.DOTALL,
        )
        matches2 = code_pattern2.findall(glossary_text)
        rarc_pattern = re.compile(
            r'([NM]A?\d{2,3})\s+(.*?)(?=(?:CO|PR|OA|PI|CR)-\d|[NM]A?\d|\Z)',
            re.DOTALL,
        )
        rarc_matches = rarc_pattern.findall(glossary_text)
        matches = matches2 + rarc_matches

    if not matches:
        return None

    major_code = ""
    major_desc = ""
    minor_code = ""
    minor_desc = ""

    for code, desc in matches:
        code = code.strip()
        desc = " ".join(desc.split()).strip()  # normalize whitespace
        # Remove trailing periods for clean summary
        desc_clean = desc.rstrip(".")

        if code.startswith(("CO-", "PR-", "OA-", "PI-", "CR-")):
            # CARC (major code)
            if not major_code:
                major_code = code
                major_desc = desc_clean
        else:
            # RARC (minor code)
            if not minor_code:
                minor_code = code
                minor_desc = desc_clean

    if not major_code and not minor_code:
        return None

    # Generate natural summary from minor description (or major if no minor)
    summary_source = minor_desc or major_desc
    missing_summary = _summarize_denial(summary_source)

    # Map what's missing to where to find it
    source, fallback = _map_artifact_source(missing_summary, major_code, minor_code)

    detail = DenialCodeDetail(
        major_code=major_code,
        major_description=major_desc,
        minor_code=minor_code,
        minor_description=minor_desc,
        missing_summary=missing_summary,
        artifact_source=source,
        artifact_source_fallback=fallback,
    )

    logger.info(
        "EOB GLOSSARY parsed",
        major_code=major_code,
        minor_code=minor_code,
        missing_summary=missing_summary,
        source=source,
    )

    return detail


def _summarize_denial(description: str) -> str:
    """
    Generate a natural short summary from the denial description.
    e.g., "Missing patient medical record for this service" → "Missing medical record"
    """
    desc_lower = description.lower()

    # Direct keyword matching for common denial patterns
    if "missing" in desc_lower and "medical record" in desc_lower:
        return "Missing medical record"
    if "missing" in desc_lower and "operative" in desc_lower:
        return "Missing operative report"
    if "missing" in desc_lower and "pathology" in desc_lower:
        return "Missing pathology report"
    if "missing" in desc_lower and "clinical documentation" in desc_lower:
        return "Missing clinical documentation"
    if "missing" in desc_lower and "documentation reference" in desc_lower:
        return "Missing authorization reference"
    if "missing" in desc_lower and "ordering provider" in desc_lower:
        return "Missing provider identifier"
    if "missing" in desc_lower and "referring provider" in desc_lower:
        return "Missing referring provider"
    if "missing" in desc_lower and "authorization" in desc_lower:
        return "Missing prior authorization"
    if "attachment" in desc_lower and "documentation" in desc_lower:
        return "Additional documentation required"
    if "lacks information" in desc_lower or "billing error" in desc_lower:
        return "Claim information incomplete"
    if "not covered" in desc_lower or "not medically necessary" in desc_lower:
        return "Medical necessity not established"
    if "timely filing" in desc_lower or "filing limit" in desc_lower:
        return "Timely filing exceeded"
    if "duplicate" in desc_lower:
        return "Duplicate claim submission"
    if "eligibility" in desc_lower or "not eligible" in desc_lower:
        return "Patient eligibility issue"
    if "prior auth" in desc_lower or "precertification" in desc_lower:
        return "Prior authorization required"
    if "coordination" in desc_lower or "other payer" in desc_lower:
        return "Coordination of benefits needed"

    # Fallback: take first meaningful phrase
    # Remove common filler words and truncate
    words = description.split()
    if len(words) <= 5:
        return description
    # Take first 5 meaningful words
    return " ".join(words[:5])


def _map_artifact_source(
    summary: str,
    major_code: str,
    minor_code: str,
) -> tuple[str, str]:
    """
    Map what's missing to WHERE to find it.
    Returns (primary_source, fallback_source).
    """
    s = summary.lower()

    # Medical records, reports, clinical docs → EHR first, then PMS
    if any(kw in s for kw in [
        "medical record", "operative report", "pathology report",
        "clinical documentation", "chart notes",
    ]):
        return "EHR", "PMS"

    # Provider identifier, NPI → PMS first, then EHR
    if any(kw in s for kw in [
        "provider identifier", "referring provider", "provider npi",
    ]):
        return "PMS", "EHR"

    # Authorization, precertification → PMS first, then EHR
    if any(kw in s for kw in [
        "authorization", "precertification", "auth reference",
    ]):
        return "PMS", "EHR"

    # Policy, eligibility, coordination → Payer Portal
    if any(kw in s for kw in [
        "eligibility", "coordination", "policy", "coverage",
    ]):
        return "Payer Portal", "PMS"

    # Additional documentation (generic CO-252) → EHR first
    if "documentation" in s or "additional" in s:
        return "EHR", "PMS"

    # Claim information incomplete (CO-16) → PMS first
    if "information" in s or "incomplete" in s:
        return "PMS", "EHR"

    # Default
    return "EHR", "PMS"


class ToolExecutionError(Exception):
    pass


# ------------------------------------------------------------------ #
# Regex patterns for EOB field extraction
# ------------------------------------------------------------------ #

# CARC: plain numeric (e.g. "97", "16") OR prefixed (CO-97, PR-96, OA-23)
_CARC_PATTERN = re.compile(
    r"\b(?:CO|PR|OA|PI|CR)-?(\d{1,3})\b|"
    r"(?:CARC|Claim Adjustment Reason Code)[:\s#]*(\d{1,3})\b",
    re.IGNORECASE,
)

# RARC: always alphanumeric starting with N, M, or MA (e.g. N130, MA04, M86)
_RARC_PATTERN = re.compile(
    r"\b((?:MA|N|M)\d{2,3})\b|"
    r"(?:RARC|Remark Code)[:\s#]*([A-Z]{1,2}\d{2,3})\b",
    re.IGNORECASE,
)

# Monetary amounts like $1,234.56 or 1234.56
_AMOUNT_PATTERN = re.compile(r"\$?([\d,]+\.\d{2})")

# Common denial remark phrases
_DENIAL_REMARK_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"(not (?:medically )?covered[^.]*\.)",
        r"(claim submitted past.*?filing.*?limit[^.]*\.)",
        r"(prior authorization.*?required[^.]*\.)",
        r"(duplicate claim[^.]*\.)",
        r"(service not.*?benefit[^.]*\.)",
        r"(invalid.*?code[^.]*\.)",
        r"(information.*?needed[^.]*\.)",
    ]
]


def _extract_with_pymupdf(pdf_path: Path) -> tuple[str, float] | None:
    """
    Try extracting text from a digital (text-layer) PDF using PyMuPDF.

    Returns (text, confidence) if enough text is found, else None
    to signal fallback to Tesseract OCR.
    """
    try:
        import fitz  # PyMuPDF

        from rcm_denial.config.settings import settings
        min_chars = settings.ocr_pymupdf_min_chars

        doc = fitz.open(str(pdf_path))
        pages_text = []
        for page in doc:
            pages_text.append(page.get_text())
        doc.close()

        full_text = "\n".join(pages_text).strip()
        if len(full_text) >= min_chars:
            logger.info(
                "PyMuPDF text extraction succeeded",
                pdf_path=str(pdf_path),
                chars=len(full_text),
            )
            return full_text, 0.95   # high confidence for digital text
        return None

    except ImportError:
        return None
    except Exception as exc:
        logger.debug("PyMuPDF extraction failed, will try Tesseract", error=str(exc))
        return None


def _extract_with_tesseract(pdf_path: Path, dpi: int = 300) -> tuple[str, float]:
    """
    Extract text from a scanned PDF using Tesseract OCR + pdf2image.
    """
    from pdf2image import convert_from_path
    import pytesseract
    from rcm_denial.config.settings import settings

    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    images = convert_from_path(str(pdf_path), dpi=dpi)
    pages_text = []
    confidences = []

    for img in images:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img)
        pages_text.append(text)

        valid_conf = [c for c in data["conf"] if c != -1]
        if valid_conf:
            confidences.append(sum(valid_conf) / len(valid_conf) / 100.0)

    full_text = "\n".join(pages_text)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return full_text, avg_confidence


def extract_text_from_pdf(pdf_path: Path, dpi: int = 300) -> tuple[str, float]:
    """
    Extracts text from a PDF.

    Strategy:
      1. Try PyMuPDF first (instant, high accuracy for digital PDFs)
      2. Fall back to Tesseract OCR (for scanned/image-only PDFs)
      3. Fall back to mock text (if OCR deps not installed)

    Returns:
        Tuple of (extracted_text, confidence_score 0-1)
    """
    # Strategy 1: PyMuPDF for digital PDFs
    pymupdf_result = _extract_with_pymupdf(pdf_path)
    if pymupdf_result is not None:
        return pymupdf_result

    # Strategy 2: Tesseract OCR for scanned PDFs
    try:
        text, confidence = _extract_with_tesseract(pdf_path, dpi=dpi)
        logger.info(
            "Tesseract OCR extraction",
            pdf_path=str(pdf_path),
            chars=len(text),
            confidence=round(confidence, 3),
        )
        return text, confidence

    except ImportError as exc:
        logger.warning("OCR dependencies not installed — using mock text", error=str(exc))
        return _get_mock_eob_text(), 0.85
    except Exception as exc:
        logger.error("PDF OCR failed", error=str(exc), pdf_path=str(pdf_path))
        raise ToolExecutionError(f"OCR extraction failed for {pdf_path}: {exc}") from exc


def _get_mock_eob_text() -> str:
    """Returns sample EOB text for testing without real PDF files."""
    return """
    EXPLANATION OF BENEFITS
    Payer: Blue Cross Blue Shield
    Claim Number: CLM-2024-001
    Member ID: BCB123456789
    Date of Service: 09/20/2024

    PROCEDURE  BILLED    ALLOWED   ADJUSTMENT  PAID    REASON
    27447      $15000.00 $0.00     $15000.00   $0.00

    Claim Adjustment Reason Code: CO-97
    CARC 97 - The benefit for this service is included in the payment/allowance for another service/procedure.

    Remark Code: N130
    RARC N130 - Alert: Missing/incomplete/invalid type of bill.

    Additional Remarks:
    Prior authorization was not obtained for this service.
    Service not covered under current benefit plan without prior authorization.

    Total Billed: $15,000.00
    Total Allowed: $0.00
    Total Paid: $0.00
    Patient Responsibility: $0.00
    """


def _extract_amounts(text: str) -> dict[str, Optional[float]]:
    """Extracts billed, allowed, adjustment and paid amounts from EOB text."""
    amounts: dict[str, Optional[float]] = {
        "billed": None, "allowed": None, "adjustment": None, "paid": None
    }
    patterns = {
        "billed": re.compile(r"(?:total\s+)?billed[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE),
        "allowed": re.compile(r"(?:total\s+)?allowed[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE),
        "adjustment": re.compile(r"(?:total\s+)?adjustment[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE),
        "paid": re.compile(r"(?:total\s+)?paid[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE),
    }
    for key, pat in patterns.items():
        m = pat.search(text)
        if m:
            try:
                amounts[key] = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return amounts


def extract_eob_data(eob_pdf_path: Optional[str]) -> EobExtractedData:
    """
    Performs OCR on the EOB PDF and extracts structured denial data.

    Args:
        eob_pdf_path: Filesystem path to the EOB PDF. If None or missing,
                      returns an empty EobExtractedData with a warning.

    Returns:
        EobExtractedData model with extracted CARC, RARC, remarks, and amounts.

    Raises:
        ToolExecutionError: On unrecoverable extraction failure.
    """
    if not eob_pdf_path:
        logger.warning("No EOB PDF path provided — skipping OCR extraction")
        return EobExtractedData(extraction_method="skipped", eob_available=False)

    pdf_path = Path(eob_pdf_path)
    eob_available = pdf_path.exists()

    if not eob_available:
        logger.warning(
            "EOB PDF file not found — CSV data will be used as fallback",
            path=str(pdf_path),
        )
        return EobExtractedData(
            extraction_method="eob_not_found",
            eob_available=False,
            raw_text_excerpt=f"EOB file not found: {pdf_path}",
        )

    logger.info("Extracting text from EOB PDF", path=str(pdf_path))
    raw_text, confidence = extract_text_from_pdf(pdf_path)

    # ── Primary extraction: GLOSSARY section (source of truth) ──────
    denial_detail = _parse_glossary(raw_text)

    # ── Secondary: regex-based extraction for backward compatibility ──
    carc_matches = _CARC_PATTERN.findall(raw_text)
    carc_codes = list({
        (m[0] or m[1]).strip().upper()
        for m in carc_matches
        if (m[0] or m[1]).strip()
    })

    rarc_matches = _RARC_PATTERN.findall(raw_text)
    rarc_codes = list({
        (m[0] or m[1]).strip().upper()
        for m in rarc_matches
        if (m[0] or m[1]).strip()
    })

    # If GLOSSARY parsing succeeded, ensure codes are consistent
    if denial_detail:
        # Use GLOSSARY codes as source of truth
        major_num = denial_detail.major_code.split("-", 1)[-1] if "-" in denial_detail.major_code else denial_detail.major_code
        if major_num and major_num not in carc_codes:
            carc_codes.insert(0, major_num)
        if denial_detail.minor_code and denial_detail.minor_code not in rarc_codes:
            rarc_codes.insert(0, denial_detail.minor_code)

    # Extract denial remarks
    denial_remarks = []
    # Use GLOSSARY descriptions as primary remarks
    if denial_detail:
        if denial_detail.major_description:
            denial_remarks.append(f"{denial_detail.major_code}: {denial_detail.major_description}")
        if denial_detail.minor_description:
            denial_remarks.append(f"{denial_detail.minor_code}: {denial_detail.minor_description}")
    # Also extract any additional remarks from the body
    for pattern in _DENIAL_REMARK_PATTERNS:
        for match in pattern.findall(raw_text):
            remark = match.strip() if isinstance(match, str) else match[0].strip()
            if remark and remark not in denial_remarks:
                denial_remarks.append(remark)

    # Extract monetary amounts
    amounts = _extract_amounts(raw_text)

    result = EobExtractedData(
        carc_codes_found=carc_codes,
        rarc_codes_found=rarc_codes,
        denial_remarks=denial_remarks,
        denial_detail=denial_detail,
        billed_amount=amounts.get("billed"),
        allowed_amount=amounts.get("allowed"),
        adjustment_amount=amounts.get("adjustment"),
        paid_amount=amounts.get("paid"),
        raw_text_excerpt=raw_text[:1500],
        ocr_confidence=confidence,
        extraction_method="pymupdf",
        eob_available=True,
    )

    logger.info(
        "EOB extraction complete",
        carc_codes=carc_codes,
        rarc_codes=rarc_codes,
        denial_detail_extracted=denial_detail is not None,
        missing_summary=denial_detail.missing_summary if denial_detail else "N/A",
        artifact_source=denial_detail.artifact_source if denial_detail else "N/A",
        remark_count=len(denial_remarks),
        confidence=round(confidence, 3),
    )
    return result
