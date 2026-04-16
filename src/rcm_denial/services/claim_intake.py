##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: claim_intake.py
# Purpose: All input claim handling in one place:
#            - FIELD_MAPS: column name mapping per source system
#            - ClaimInputRecord: Pydantic model that parses/validates
#              one CSV row (amounts, codes, %, booleans, dates)
#            - stream_claims(): main entry point — reads CSV, maps
#              fields, validates rows, records to DB, yields
#              ClaimRecord objects (urgent first, then normal)
#            - get_intake_report(): query DB for intake results
#
##########################################################

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from rcm_denial.models.claim import ClaimRecord
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

# Claims with <= this many days to deadline are treated as urgent
URGENCY_DAYS_THRESHOLD: int = 7

# Priority label values that trigger urgent processing
URGENCY_PRIORITY_LABELS: set[str] = {"HIGH"}


# ------------------------------------------------------------------ #
# Field maps — external CSV column names → internal ClaimInputRecord names
#
# Fields NOT listed here pass through unchanged (same name in CSV and
# in our system). To onboard a new hospital or EHR system:
#   1. Add a new entry to FIELD_MAPS with that system's column names
#   2. Pass --source <key> when running process-batch
#   Zero other code changes needed.
# ------------------------------------------------------------------ #

FIELD_MAPS: dict[str, dict[str, str]] = {
    "claim_db": {
        # CSV column       → internal field name
        "rec_id":          "claim_id",
        "service_date":    "date_of_service",
        "denial_code":     "carc_code",
        "cpt_code":        "cpt_codes",
        "diagnosis_code":  "diagnosis_codes",
        "member_id":       "patient_id",
        "provider_npi":    "provider_id",
        "payer":           "payer_name",
    },

    # ── Future integrations — add new dicts here ──────────────────── #
    # "epic": {
    #     "claimNumber":    "claim_id",
    #     "dateOfService":  "date_of_service",
    #     "adjustCode":     "carc_code",
    #     "procedureCode":  "cpt_codes",
    #     "icd10":          "diagnosis_codes",
    #     "memberId":       "patient_id",
    #     "npi":            "provider_id",
    #     "payerName":      "payer_name",
    # },
    # "cerner": {
    #     ...
    # },
}


# ------------------------------------------------------------------ #
# Exception
# ------------------------------------------------------------------ #

class ClaimValidationError(Exception):
    """
    Raised when on_error='stop' and a CSV row fails Pydantic validation.
    Carries structured error details for clear user feedback.
    """

    def __init__(self, row_number: int, claim_id: str, errors: list[str]) -> None:
        self.row_number = row_number
        self.claim_id = claim_id
        self.errors = errors
        bullet_list = "\n".join(f"  • {e}" for e in errors)
        super().__init__(
            f"Row {row_number} (claim_id={claim_id!r}) failed validation:\n{bullet_list}"
        )


# ------------------------------------------------------------------ #
# SQLite — claim_intake_log
# ------------------------------------------------------------------ #

def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def _init_db() -> None:
    """Creates all required tables if they do not exist."""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_intake_log (
                id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
                batch_id           TEXT,
                source_file        TEXT,
                row_number         INTEGER,
                claim_id           TEXT,
                status             TEXT     NOT NULL,   -- 'valid' | 'rejected'
                rejection_reasons  TEXT,                -- JSON array of error strings
                raw_data           TEXT,                -- JSON of original CSV row
                recorded_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_audit_log (
                id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
                batch_id           TEXT,
                run_id             TEXT,
                claim_id           TEXT     NOT NULL,
                node_name          TEXT     NOT NULL,
                status             TEXT     NOT NULL,   -- 'started'|'completed'|'failed'|'skipped'
                details            TEXT,
                duration_ms        REAL,
                token_usage        TEXT,                -- JSON dict
                recorded_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_pipeline_result (
                id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
                batch_id           TEXT,
                run_id             TEXT,
                claim_id           TEXT     NOT NULL,
                carc_code          TEXT,
                denial_category    TEXT,
                recommended_action TEXT,
                final_status       TEXT,                -- 'complete'|'partial'|'failed'
                package_type       TEXT,
                errors             TEXT,                -- JSON array
                pipeline_errors    TEXT,                -- JSON array from state.errors
                duration_ms        REAL,
                llm_calls          INTEGER  DEFAULT 0,
                recorded_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Gap 37 — medical record source registry
        # Tracks how to reach each provider's EHR/EMR: REST API, FHIR, RPA portal, or manual.
        # One row per provider_id; updated whenever connectivity is (re)verified.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS medical_record_source_registry (
                id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
                provider_id        TEXT     NOT NULL UNIQUE,
                provider_name      TEXT,
                access_method      TEXT     NOT NULL DEFAULT 'mock',
                                             -- 'mock' | 'fhir_r4' | 'epic_api' | 'cerner_api'
                                             -- | 'athena_api' | 'rpa_portal' | 'manual'
                endpoint_url       TEXT,     -- base URL for REST/FHIR calls
                credentials_ref    TEXT,     -- key name in secrets manager / .env
                last_verified_at   TIMESTAMP,
                last_verified_ok   INTEGER  DEFAULT 1,  -- 1 = healthy, 0 = error
                notes              TEXT,
                registered_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def _record_intake(
    *,
    batch_id: str,
    source_file: str,
    row_number: int,
    claim_id: str,
    status: Literal["valid", "rejected"],
    rejection_reasons: list[str],
    raw_data: dict,
) -> None:
    """Inserts one row into claim_intake_log."""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO claim_intake_log
                (batch_id, source_file, row_number, claim_id,
                 status, rejection_reasons, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                source_file,
                row_number,
                claim_id,
                status,
                json.dumps(rejection_reasons),
                json.dumps(raw_data, default=str),
            ),
        )
        conn.commit()


# ------------------------------------------------------------------ #
# ClaimInputRecord — parses and validates one CSV row
#
# All parsing (dollar amounts, code prefixes, percentages, booleans)
# happens here via field_validators.  Once validated, convert to the
# pipeline ClaimRecord via .to_claim_record().
# ------------------------------------------------------------------ #

class ClaimInputRecord(BaseModel):
    """
    Internal canonical form of one CSV row after field mapping.
    All field names are our internal names (after FIELD_MAPS applied).
    """

    # ---- Required ----
    claim_id: str
    patient_id: str
    payer_id: str
    provider_id: str
    date_of_service: date
    cpt_codes: list[str]
    diagnosis_codes: list[str]
    carc_code: str
    denial_date: date
    billed_amount: float = Field(..., gt=0)

    # ---- Optional ----
    denial_reason: Optional[str] = None        # always None at input; OCR fills it later
    rarc_code: Optional[str] = None
    eob_pdf_path: Optional[str] = None

    # Patient
    patient_name: Optional[str] = None
    patient_dob: Optional[date] = None
    member_id: Optional[str] = None            # original member ID from payer

    # Service
    invoice_number: Optional[str] = None
    status: Optional[str] = None
    cpt_description: Optional[str] = None
    specialty: Optional[str] = None
    facility_type: Optional[str] = None

    # Provider
    provider_npi: Optional[str] = None
    rendering_provider: Optional[str] = None

    # Payer
    payer_name: Optional[str] = None
    payer_phone: Optional[str] = None
    payer_portal_url: Optional[str] = None
    payer_response_time_days: Optional[int] = None
    ivr_style: Optional[str] = None
    primary_channel: Optional[str] = None
    payer_filing_deadline_days: Optional[int] = None

    # Clinical
    requires_auth: Optional[bool] = None

    # Financial
    contracted_rate: Optional[float] = None
    paid_amount: Optional[float] = None

    # AR / workflow
    days_in_ar: Optional[int] = None
    prior_appeal_attempts: int = 0
    appealable: bool = True
    rebillable: bool = True
    appeal_deadline: Optional[date] = None
    days_to_deadline: Optional[int] = None

    # Priority
    appeal_win_probability: Optional[float] = None
    priority_score: Optional[float] = None
    priority_label: Optional[str] = None

    # ---------------------------------------------------------------- #
    # Field validators
    # ---------------------------------------------------------------- #

    @field_validator("cpt_codes", "diagnosis_codes", mode="before")
    @classmethod
    def parse_code_list(cls, v: Any) -> list[str]:
        """Handles single string or comma-separated values → list."""
        if isinstance(v, list):
            return [str(c).strip() for c in v if str(c).strip()]
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return []

    @field_validator(
        "date_of_service", "denial_date", "patient_dob", "appeal_deadline",
        mode="before",
    )
    @classmethod
    def parse_flexible_date(cls, v: Any) -> Optional[date]:
        """
        Accepts multiple date formats:
          YYYY-MM-DD  (ISO standard)
          DD-MM-YYYY  (common in spreadsheets)
          MM-DD-YYYY  (US format)
          DD/MM/YYYY, MM/DD/YYYY, YYYY/MM/DD
          Also handles pandas Timestamp objects.
        """
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, date):
            return v

        from datetime import datetime as dt

        text = str(v).strip()

        # Try common formats in order of specificity
        formats = [
            "%Y-%m-%d",     # 2025-08-15 (ISO)
            "%d-%m-%Y",     # 15-08-2025 (DD-MM-YYYY)
            "%m-%d-%Y",     # 08-15-2025 (MM-DD-YYYY)
            "%Y/%m/%d",     # 2025/08/15
            "%d/%m/%Y",     # 15/08/2025
            "%m/%d/%Y",     # 08/15/2025
            "%Y-%m-%dT%H:%M:%S",  # ISO with time
            "%d-%b-%Y",     # 15-Aug-2025
            "%b %d, %Y",   # Aug 15, 2025
        ]

        for fmt in formats:
            try:
                return dt.strptime(text, fmt).date()
            except ValueError:
                continue

        # Last resort: let Pydantic try its default parsing
        return v

    @field_validator("billed_amount", "contracted_rate", "paid_amount", mode="before")
    @classmethod
    def parse_currency(cls, v: Any) -> Optional[float]:
        """'$4,250.00' → 4250.0.  Empty string / None → None."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        cleaned = str(v).replace("$", "").replace(",", "").strip()
        return float(cleaned) if cleaned else None

    @field_validator("carc_code", mode="before")
    @classmethod
    def normalize_carc(cls, v: Any) -> str:
        """'CO-252' → '252', 'PR-4' → '4', '97' → '97'."""
        code = str(v).strip().upper()
        for prefix in ("CO-", "PR-", "OA-", "PI-", "CR-"):
            if code.startswith(prefix):
                return code[len(prefix):]
        return code

    @field_validator("appeal_win_probability", mode="before")
    @classmethod
    def parse_percentage(cls, v: Any) -> Optional[float]:
        """'51%' → 0.51.  Already 0–1 float passes through."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, (int, float)):
            val = float(v)
            return val if val <= 1.0 else val / 100.0
        cleaned = str(v).replace("%", "").strip()
        return float(cleaned) / 100.0 if cleaned else None

    @field_validator("requires_auth", "appealable", "rebillable", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> Optional[bool]:
        """'True'/'False'/'true'/'1'/'yes' → bool."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes")

    # ---------------------------------------------------------------- #
    # Cross-field validation
    # ---------------------------------------------------------------- #

    @model_validator(mode="after")
    def check_date_order(self) -> "ClaimInputRecord":
        """denial_date must not be before date_of_service."""
        if self.denial_date < self.date_of_service:
            raise ValueError(
                f"denial_date ({self.denial_date}) cannot be before "
                f"date_of_service ({self.date_of_service})"
            )
        return self

    # ---------------------------------------------------------------- #
    # Conversion to pipeline model
    # ---------------------------------------------------------------- #

    def to_claim_record(self) -> ClaimRecord:
        """
        Converts this validated input record to the pipeline ClaimRecord.
        Also populates member_id from patient_id for reference.
        """
        data = self.model_dump()
        # Ensure member_id is preserved — patient_id was mapped from member_id
        if not data.get("member_id"):
            data["member_id"] = data.get("patient_id")
        # Also carry provider_npi from provider_id mapping
        if not data.get("provider_npi"):
            data["provider_npi"] = data.get("provider_id")
        return ClaimRecord(**data)


# ------------------------------------------------------------------ #
# Private helpers
# ------------------------------------------------------------------ #

def _apply_mapping(raw_row: dict, source: str) -> dict:
    """
    Renames CSV column keys using FIELD_MAPS[source].
    Keys not in the map pass through unchanged.
    Unknown source → passthrough with a warning (assumes CSV already uses our names).
    """
    field_map = FIELD_MAPS.get(source, {})
    if not field_map and source not in ("passthrough",):
        logger.warning(
            "Unknown source — using passthrough mapping (CSV names = internal names)",
            source=source,
            known_sources=list(FIELD_MAPS.keys()),
        )
    return {field_map.get(k, k): v for k, v in raw_row.items()}


def _is_urgent(raw_row: dict) -> bool:
    """
    Checks the RAW (pre-mapping) row to determine urgency.
    Returns True if priority_label is in URGENCY_PRIORITY_LABELS
    OR days_to_deadline <= URGENCY_DAYS_THRESHOLD.
    """
    label = str(raw_row.get("priority_label", "")).strip().upper()
    if label in URGENCY_PRIORITY_LABELS:
        return True
    try:
        days = raw_row.get("days_to_deadline")
        if days is not None and int(days) <= URGENCY_DAYS_THRESHOLD:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _validate_and_convert(
    *,
    mapped_row: dict,
    raw_row: dict,
    row_number: int,
    batch_id: str,
    source_file: str,
    on_error: Literal["proceed", "stop"],
) -> Optional[ClaimRecord]:
    """
    Validates one mapped row via ClaimInputRecord Pydantic model.
    Records the outcome (valid / rejected + reasons) to DB.
    Returns ClaimRecord on success, None on failure.
    Raises ClaimValidationError if on_error='stop' and validation fails.
    """
    # Try to get claim_id for DB logging even if validation fails
    claim_id = str(mapped_row.get("claim_id") or raw_row.get("rec_id", "")).strip()

    try:
        input_record = ClaimInputRecord(**mapped_row)
        claim = input_record.to_claim_record()

        _record_intake(
            batch_id=batch_id,
            source_file=source_file,
            row_number=row_number,
            claim_id=claim_id,
            status="valid",
            rejection_reasons=[],
            raw_data=raw_row,
        )

        logger.debug("Row valid", row=row_number, claim_id=claim_id)
        return claim

    except (ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            errors = [
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            ]
        else:
            errors = [str(exc)]

        _record_intake(
            batch_id=batch_id,
            source_file=source_file,
            row_number=row_number,
            claim_id=claim_id,
            status="rejected",
            rejection_reasons=errors,
            raw_data=raw_row,
        )

        logger.warning(
            "Row rejected",
            row=row_number,
            claim_id=claim_id,
            error_count=len(errors),
            errors=errors,
        )

        if on_error == "stop":
            raise ClaimValidationError(row_number, claim_id, errors)

        return None


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def stream_claims(
    csv_path: Path | str,
    source: str = "claim_db",
    on_error: Literal["proceed", "stop"] = "proceed",
    batch_id: str = "",
) -> Iterator[ClaimRecord]:
    """
    Main entry point for reading claims from a CSV.

    Reads the CSV row by row, applies field mapping, validates each row,
    records every outcome to the DB, and yields ClaimRecord objects.
    Urgent claims (HIGH priority or deadline <= 7 days) are yielded first.

    Args:
        csv_path:  Path to input CSV file.
        source:    Key into FIELD_MAPS for column renaming.
                   'claim_db' matches our default format.
                   Add new keys to FIELD_MAPS for other systems.
        on_error:  'proceed' — log bad rows to DB, continue (default).
                   'stop'    — raise ClaimValidationError on first bad row.
                               Use this when testing a new hospital integration.
        batch_id:  Identifier stored in DB for reporting. Auto-set by
                   batch_processor if not provided.

    Yields:
        ClaimRecord objects — urgent first, then normal order.

    Raises:
        FileNotFoundError:    csv_path does not exist.
        ClaimValidationError: on_error='stop' and a row fails validation.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    _init_db()

    source_file = str(csv_path)
    urgent: list[ClaimRecord] = []
    normal: list[ClaimRecord] = []
    total = valid_count = rejected_count = 0

    logger.info(
        "Claim intake starting",
        csv_path=source_file,
        source=source,
        on_error=on_error,
        batch_id=batch_id,
    )

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row_number, raw_row in enumerate(reader, start=1):
            total += 1
            raw_row = dict(raw_row)

            # Apply field name mapping for this source system
            mapped = _apply_mapping(raw_row, source)

            # Validate + record to DB
            claim = _validate_and_convert(
                mapped_row=mapped,
                raw_row=raw_row,
                row_number=row_number,
                batch_id=batch_id,
                source_file=source_file,
                on_error=on_error,
            )

            if claim is None:
                rejected_count += 1
                continue

            valid_count += 1

            # Priority split using raw row (priority_label field same in all sources)
            if _is_urgent(raw_row):
                urgent.append(claim)
            else:
                normal.append(claim)

    logger.info(
        "Claim intake complete",
        total=total,
        valid=valid_count,
        rejected=rejected_count,
        urgent=len(urgent),
        normal=len(normal),
        batch_id=batch_id,
    )

    # Yield urgent claims first, then normal
    yield from urgent
    yield from normal


def get_intake_report(
    batch_id: str = "",
    source_file: str = "",
) -> dict:
    """
    Queries the DB for intake results and returns a structured report.

    Args:
        batch_id:    Filter by batch ID. Empty = all batches.
        source_file: Filter by CSV path. Empty = all files.

    Returns:
        dict with keys: batch_id, source_file, total, valid, rejected,
        rejected_details (list of {row_number, claim_id, errors, raw_data, recorded_at})
    """
    _init_db()

    # Build WHERE clause dynamically
    conditions: list[str] = []
    params: list[Any] = []
    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if source_file:
        conditions.append("source_file = ?")
        params.append(source_file)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        # Counts per status
        summary_rows = conn.execute(
            f"SELECT status, COUNT(*) AS cnt FROM claim_intake_log {where} GROUP BY status",
            params,
        ).fetchall()

        # Rejected details
        rejected_conditions = conditions + ["status = 'rejected'"]
        rejected_where = "WHERE " + " AND ".join(rejected_conditions)
        rejected_params = params + []

        rejected_rows = conn.execute(
            f"""
            SELECT row_number, claim_id, rejection_reasons, raw_data, recorded_at
            FROM   claim_intake_log
            {rejected_where}
            ORDER  BY row_number
            """,
            rejected_params,
        ).fetchall()

    counts = {row["status"]: row["cnt"] for row in summary_rows}

    return {
        "batch_id":    batch_id,
        "source_file": source_file,
        "total":       sum(counts.values()),
        "valid":       counts.get("valid", 0),
        "rejected":    counts.get("rejected", 0),
        "rejected_details": [
            {
                "row_number":  r["row_number"],
                "claim_id":    r["claim_id"],
                "errors":      json.loads(r["rejection_reasons"] or "[]"),
                "raw_data":    json.loads(r["raw_data"] or "{}"),
                "recorded_at": r["recorded_at"],
            }
            for r in rejected_rows
        ],
    }


# ------------------------------------------------------------------ #
# Audit log persistence (Gap 41)
# ------------------------------------------------------------------ #

def persist_audit_log(
    *,
    batch_id: str,
    run_id: str,
    claim_id: str,
    audit_entries: list,
) -> None:
    """
    Writes pipeline audit entries for one claim to claim_audit_log.
    Called after each claim completes processing.

    Args:
        batch_id:     Batch identifier.
        run_id:       Unique run ID for this claim.
        claim_id:     Claim being processed.
        audit_entries: List of AuditEntry objects from state.audit_log.
    """
    _init_db()
    rows = []
    for entry in audit_entries:
        rows.append((
            batch_id,
            run_id,
            claim_id,
            entry.node_name,
            entry.status,
            entry.details,
            entry.duration_ms,
            json.dumps(entry.token_usage) if entry.token_usage else None,
        ))

    with sqlite3.connect(_get_db_path()) as conn:
        conn.executemany(
            """
            INSERT INTO claim_audit_log
                (batch_id, run_id, claim_id, node_name, status,
                 details, duration_ms, token_usage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def persist_pipeline_result(
    *,
    batch_id: str,
    run_id: str,
    claim_id: str,
    carc_code: str,
    denial_category: str,
    recommended_action: str,
    final_status: str,
    package_type: str,
    errors: list[str],
    pipeline_errors: list[str],
    duration_ms: float,
    llm_calls: int = 0,
) -> None:
    """
    Writes the pipeline outcome for one claim to claim_pipeline_result.
    Enables batch statistics and cross-batch historical reporting.
    """
    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO claim_pipeline_result
                (batch_id, run_id, claim_id, carc_code, denial_category,
                 recommended_action, final_status, package_type,
                 errors, pipeline_errors, duration_ms, llm_calls)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id, run_id, claim_id, carc_code, denial_category,
                recommended_action, final_status, package_type,
                json.dumps(errors), json.dumps(pipeline_errors),
                duration_ms, llm_calls,
            ),
        )
        # Gap 19 — payer submission registry
        # Maps payer_id → submission method: how to deliver the package to each payer.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payer_submission_registry (
                id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
                payer_id            TEXT     NOT NULL UNIQUE,
                payer_name          TEXT,
                submission_method   TEXT     NOT NULL DEFAULT 'mock',
                                         -- 'mock' | 'availity_api' | 'change_healthcare_api'
                                         -- | 'rpa_portal' | 'edi_837' | 'mail'
                api_endpoint        TEXT,
                credentials_ref     TEXT,    -- key in secrets manager / .env
                portal_url          TEXT,
                clearinghouse_id    TEXT,    -- for EDI 837 submissions
                is_active           INTEGER  DEFAULT 1,
                notes               TEXT,
                registered_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Gap 25 — submission log
        # Tracks every submission attempt per claim including retries.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submission_log (
                id                    INTEGER  PRIMARY KEY AUTOINCREMENT,
                run_id                TEXT     NOT NULL,
                claim_id              TEXT     NOT NULL,
                batch_id              TEXT,
                payer_id              TEXT,
                submission_method     TEXT,
                attempt_number        INTEGER  DEFAULT 1,
                status                TEXT     NOT NULL,
                                           -- 'submitted' | 'failed' | 'rejected' | 'pending'
                response_code         TEXT,
                response_message      TEXT,
                confirmation_number   TEXT,
                package_type          TEXT,
                pdf_path              TEXT,
                submitted_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                response_received_at  TIMESTAMP
            )
        """)
        # Gap 49 — LLM cost log
        # Tracks token usage and cost per LLM call for budget monitoring.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_cost_log (
                id              INTEGER  PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT     NOT NULL,
                batch_id        TEXT     NOT NULL DEFAULT '',
                agent_name      TEXT     NOT NULL,
                model           TEXT     NOT NULL,
                input_tokens    INTEGER  NOT NULL DEFAULT 0,
                output_tokens   INTEGER  NOT NULL DEFAULT 0,
                cost_usd        REAL     NOT NULL DEFAULT 0.0,
                recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


# ------------------------------------------------------------------ #
# Gap 37 — medical_record_source_registry helpers
# ------------------------------------------------------------------ #

def register_medical_record_source(
    *,
    provider_id: str,
    provider_name: str = "",
    access_method: str = "mock",
    endpoint_url: str = "",
    credentials_ref: str = "",
    notes: str = "",
) -> None:
    """
    Upsert a provider's EHR connectivity record.

    Called once per new provider when a batch is first processed, or
    whenever the access method / credentials change.

    access_method values:
      'mock'       — use MockEMRAdapter (dev / test)
      'fhir_r4'    — generic FHIR R4 REST (Epic/Cerner/Azure)
      'epic_api'   — Epic-specific FHIR extensions
      'cerner_api' — Cerner Millennium API
      'athena_api' — Athena Health REST
      'rpa_portal' — Playwright/Selenium browser automation
      'manual'     — request records manually; pipeline proceeds without EHR
    """
    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO medical_record_source_registry
                (provider_id, provider_name, access_method,
                 endpoint_url, credentials_ref, notes,
                 last_verified_ok, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(provider_id) DO UPDATE SET
                provider_name    = excluded.provider_name,
                access_method    = excluded.access_method,
                endpoint_url     = excluded.endpoint_url,
                credentials_ref  = excluded.credentials_ref,
                notes            = excluded.notes,
                updated_at       = CURRENT_TIMESTAMP
            """,
            (provider_id, provider_name, access_method,
             endpoint_url, credentials_ref, notes),
        )
        conn.commit()


def get_medical_record_source(provider_id: str) -> dict | None:
    """
    Returns the registry row for a provider, or None if not registered.
    Used by the EHR session manager to choose the correct adapter.
    """
    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM medical_record_source_registry WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_medical_record_source_status(provider_id: str, ok: bool) -> None:
    """Updates last_verified_ok and last_verified_at for a provider."""
    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            UPDATE medical_record_source_registry
               SET last_verified_ok = ?,
                   last_verified_at = CURRENT_TIMESTAMP,
                   updated_at       = CURRENT_TIMESTAMP
             WHERE provider_id = ?
            """,
            (1 if ok else 0, provider_id),
        )
        conn.commit()
