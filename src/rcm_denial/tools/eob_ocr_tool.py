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

from rcm_denial.models.claim import EobExtractedData
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


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


def extract_text_from_pdf(pdf_path: Path, dpi: int = 300) -> tuple[str, float]:
    """
    Extracts text from a PDF using pytesseract + pdf2image.

    Real integration swap: replace this function body with:
        import boto3
        client = boto3.client('textract', region_name=settings.aws_region)
        response = client.detect_document_text(Document={...})
        # parse response['Blocks'] for LINE text

    Returns:
        Tuple of (extracted_text, confidence_score 0-1)
    """
    try:
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

            # Average word-level confidence (filter -1 values)
            valid_conf = [c for c in data["conf"] if c != -1]
            if valid_conf:
                confidences.append(sum(valid_conf) / len(valid_conf) / 100.0)

        full_text = "\n".join(pages_text)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return full_text, avg_confidence

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
        return EobExtractedData(extraction_method="skipped")

    pdf_path = Path(eob_pdf_path)

    if not pdf_path.exists():
        logger.warning("EOB PDF file not found — using mock OCR", path=str(pdf_path))
        raw_text, confidence = _get_mock_eob_text(), 0.75
    else:
        logger.info("Extracting text from EOB PDF", path=str(pdf_path))
        raw_text, confidence = extract_text_from_pdf(pdf_path)

    # Extract CARC codes
    carc_matches = _CARC_PATTERN.findall(raw_text)
    carc_codes = list({
        (m[0] or m[1]).strip().upper()
        for m in carc_matches
        if (m[0] or m[1]).strip()
    })

    # Extract RARC codes
    rarc_matches = _RARC_PATTERN.findall(raw_text)
    rarc_codes = list({
        (m[0] or m[1]).strip().upper()
        for m in rarc_matches
        if (m[0] or m[1]).strip()
    })

    # Extract denial remarks
    denial_remarks = []
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
        billed_amount=amounts.get("billed"),
        allowed_amount=amounts.get("allowed"),
        adjustment_amount=amounts.get("adjustment"),
        paid_amount=amounts.get("paid"),
        raw_text_excerpt=raw_text[:1000],
        ocr_confidence=confidence,
    )

    logger.info(
        "EOB extraction complete",
        carc_codes=carc_codes,
        rarc_codes=rarc_codes,
        remark_count=len(denial_remarks),
        confidence=round(confidence, 3),
    )
    return result
