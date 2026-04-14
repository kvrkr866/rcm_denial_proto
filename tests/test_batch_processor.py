##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: test_batch_processor.py
# Purpose: Integration tests for the batch processing engine.
#          Tests CSV reading, idempotency, error handling,
#          and BatchReport generation using the sample CSV.
#
##########################################################

from __future__ import annotations

import csv
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def sample_csv_path() -> Path:
    return Path(__file__).parent.parent / "data" / "sample_denials.csv"


@pytest.fixture
def minimal_csv(tmp_path) -> Path:
    """Creates a minimal valid CSV with one claim for fast testing."""
    csv_file = tmp_path / "test_claims.csv"
    rows = [
        {
            "claim_id": "TEST-BATCH-001",
            "patient_id": "PAT001",
            "payer_id": "BCBS",
            "provider_id": "PROV-NPI-001",
            "date_of_service": "2024-09-20",
            "cpt_codes": "27447",
            "diagnosis_codes": "M17.11",
            "denial_reason": "Prior authorization not obtained",
            "carc_code": "97",
            "rarc_code": "N4",
            "denial_date": "2024-10-05",
            "billed_amount": "15000.00",
            "eob_pdf_path": "",
        }
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return csv_file


@pytest.fixture
def malformed_csv(tmp_path) -> Path:
    """Creates a CSV with one valid and one malformed claim."""
    csv_file = tmp_path / "malformed.csv"
    rows = [
        {
            "claim_id": "GOOD-001",
            "patient_id": "PAT001",
            "payer_id": "BCBS",
            "provider_id": "PROV-001",
            "date_of_service": "2024-09-20",
            "cpt_codes": "99213",
            "diagnosis_codes": "Z00.00",
            "denial_reason": "Coding error",
            "carc_code": "11",
            "rarc_code": "",
            "denial_date": "2024-10-01",
            "billed_amount": "285.00",
            "eob_pdf_path": "",
        },
        {
            "claim_id": "BAD-001",
            "patient_id": "",
            "payer_id": "BCBS",
            "provider_id": "",
            "date_of_service": "NOT-A-DATE",   # Invalid date
            "cpt_codes": "",
            "diagnosis_codes": "",
            "denial_reason": "",
            "carc_code": "",
            "rarc_code": "",
            "denial_date": "NOT-A-DATE",
            "billed_amount": "-100",            # Invalid amount
            "eob_pdf_path": "",
        },
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return csv_file


# ------------------------------------------------------------------ #
# CSV reader tests
# ------------------------------------------------------------------ #

class TestCsvReader:
    def test_reads_all_rows(self, sample_csv_path):
        from rcm_denial.workflows.batch_processor import _read_claims_csv
        rows = list(_read_claims_csv(sample_csv_path))
        assert len(rows) == 5

    def test_rows_have_expected_fields(self, sample_csv_path):
        from rcm_denial.workflows.batch_processor import _read_claims_csv
        rows = list(_read_claims_csv(sample_csv_path))
        required_fields = {"claim_id", "patient_id", "payer_id", "carc_code", "billed_amount"}
        for row in rows:
            assert required_fields.issubset(row.keys())

    def test_nonexistent_file_raises(self):
        from rcm_denial.workflows.batch_processor import _read_claims_csv
        with pytest.raises(FileNotFoundError):
            list(_read_claims_csv(Path("/nonexistent/file.csv")))


# ------------------------------------------------------------------ #
# Idempotency tests
# ------------------------------------------------------------------ #

class TestIdempotency:
    def test_is_already_processed_false_when_no_dir(self, tmp_path):
        from rcm_denial.workflows.batch_processor import _is_already_processed
        result = _is_already_processed("CLM-999", tmp_path)
        assert result is False

    def test_is_already_processed_false_when_no_metadata(self, tmp_path):
        from rcm_denial.workflows.batch_processor import _is_already_processed
        claim_dir = tmp_path / "CLM-001"
        claim_dir.mkdir()
        result = _is_already_processed("CLM-001", tmp_path)
        assert result is False

    def test_is_already_processed_true_when_complete_metadata(self, tmp_path):
        from rcm_denial.workflows.batch_processor import _is_already_processed
        claim_dir = tmp_path / "CLM-001"
        claim_dir.mkdir()
        metadata = {"package_type": "resubmission", "status": "complete"}
        with open(claim_dir / "submission_metadata.json", "w") as f:
            json.dump(metadata, f)
        result = _is_already_processed("CLM-001", tmp_path)
        assert result is True

    def test_is_already_processed_false_when_failed(self, tmp_path):
        from rcm_denial.workflows.batch_processor import _is_already_processed
        claim_dir = tmp_path / "CLM-001"
        claim_dir.mkdir()
        metadata = {"package_type": "failed"}
        with open(claim_dir / "submission_metadata.json", "w") as f:
            json.dump(metadata, f)
        result = _is_already_processed("CLM-001", tmp_path)
        assert result is False


# ------------------------------------------------------------------ #
# Batch processor integration tests
# ------------------------------------------------------------------ #

class TestBatchProcessor:
    def test_batch_processes_minimal_csv(self, minimal_csv, tmp_path, monkeypatch):
        """Tests that a valid single-claim CSV is fully processed."""
        # Point output to temp dir
        from rcm_denial.config import settings as settings_module
        monkeypatch.setattr(settings_module.settings, "output_dir", tmp_path)
        monkeypatch.setattr(settings_module.settings, "skip_completed_claims", False)

        from rcm_denial.workflows.batch_processor import process_batch
        report = process_batch(minimal_csv, batch_id="TEST-BATCH", skip_completed=False)

        assert report.total_claims == 1
        assert report.failed == 0 or report.completed == 1 or report.partial == 1

    def test_batch_never_aborts_on_malformed_claim(self, malformed_csv, tmp_path, monkeypatch):
        """Batch must continue processing even if one claim has a parse error."""
        from rcm_denial.config import settings as settings_module
        monkeypatch.setattr(settings_module.settings, "output_dir", tmp_path)

        from rcm_denial.workflows.batch_processor import process_batch
        report = process_batch(malformed_csv, batch_id="TEST-MALFORMED", skip_completed=False)

        assert report.total_claims == 2
        # Good claim should be processed, bad claim should be marked failed
        statuses = {r.claim_id: r.status for r in report.claim_results}
        assert statuses.get("BAD-001") == "failed"

    def test_batch_report_has_correct_totals(self, minimal_csv, tmp_path, monkeypatch):
        from rcm_denial.config import settings as settings_module
        monkeypatch.setattr(settings_module.settings, "output_dir", tmp_path)

        from rcm_denial.workflows.batch_processor import process_batch
        report = process_batch(minimal_csv, batch_id="TEST-TOTALS", skip_completed=False)

        total = report.completed + report.partial + report.failed + report.skipped
        assert total == report.total_claims

    def test_batch_writes_summary_json(self, minimal_csv, tmp_path, monkeypatch):
        from rcm_denial.config import settings as settings_module
        monkeypatch.setattr(settings_module.settings, "output_dir", tmp_path)

        from rcm_denial.workflows.batch_processor import process_batch
        report = process_batch(minimal_csv, batch_id="SUMMARY-TEST", skip_completed=False)

        summary_file = tmp_path / f"batch_summary_{report.batch_id}.json"
        assert summary_file.exists()

        with open(summary_file) as f:
            summary = json.load(f)
        assert summary["total_claims"] == 1

    def test_nonexistent_csv_raises(self):
        from rcm_denial.workflows.batch_processor import process_batch
        with pytest.raises(FileNotFoundError):
            process_batch("/nonexistent/claims.csv")

    def test_success_rate_calculation(self):
        from rcm_denial.models.output import BatchReport
        report = BatchReport(
            batch_id="TEST",
            input_csv="test.csv",
            total_claims=10,
            completed=8,
            partial=1,
            failed=1,
        )
        assert report.success_rate == 80.0
