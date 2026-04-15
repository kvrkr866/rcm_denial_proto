##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: test_submission.py
# Purpose: Unit tests for Phase 5 submission adapters,
#          mock adapter submit/check_status, submission
#          registry, and retry-on-transient-error logic.
#
##########################################################

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rcm_denial.models.claim import ClaimRecord
from rcm_denial.models.output import DenialWorkflowState, SubmissionPackage
from rcm_denial.models.submission import SubmissionResult, SubmissionStatus


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

def _make_state(claim_id: str = "CLM-SUB-001") -> DenialWorkflowState:
    claim = ClaimRecord(
        claim_id=claim_id,
        patient_id="PAT001",
        payer_id="BCBS",
        provider_id="PROV-001",
        date_of_service=date(2024, 9, 20),
        cpt_codes=["99213"],
        diagnosis_codes=["Z00.00"],
        carc_code="97",
        denial_date=date(2024, 10, 1),
        billed_amount=5000.0,
    )
    state = DenialWorkflowState.create(claim, batch_id="BATCH-SUB-001")
    state.output_package = SubmissionPackage(
        claim_id=claim_id,
        run_id=state.run_id,
        output_dir=f"./output/{claim_id}",
        package_type="appeal",
        status="complete",
    )
    return state


@pytest.fixture(autouse=True)
def _use_isolated_db(isolated_data_dir):
    yield


# ──────────────────────────────────────────────────────────────────────
# SubmissionResult / SubmissionStatus models
# ──────────────────────────────────────────────────────────────────────

class TestSubmissionModels:
    def test_result_success(self):
        result = SubmissionResult(
            success=True,
            confirmation_number="CONF-001",
            submission_method="mock",
        )
        assert result.success is True
        assert result.confirmation_number == "CONF-001"

    def test_result_failure(self):
        result = SubmissionResult(
            success=False,
            submission_method="mock",
            error_detail="Timeout",
        )
        assert result.success is False
        assert "Timeout" in result.error_detail

    def test_status_fields(self):
        status = SubmissionStatus(
            confirmation_number="CONF-001",
            payer_status="received",
        )
        assert status.payer_status == "received"


# ──────────────────────────────────────────────────────────────────────
# MockSubmissionAdapter
# ──────────────────────────────────────────────────────────────────────

class TestMockAdapter:
    def test_submit_returns_success(self, tmp_path):
        from rcm_denial.services.submission_adapters import MockSubmissionAdapter
        adapter = MockSubmissionAdapter()
        state = _make_state("CLM-MOCK-001")
        result = adapter.submit(state.output_package, state)
        assert result.success is True
        assert result.confirmation_number != ""
        assert result.submission_method == "mock"

    def test_submit_writes_receipt(self, tmp_path):
        from rcm_denial.services.submission_adapters import MockSubmissionAdapter
        adapter = MockSubmissionAdapter()
        state = _make_state("CLM-MOCK-002")
        out_dir = tmp_path / "CLM-MOCK-002"
        out_dir.mkdir(parents=True, exist_ok=True)
        state.output_package.output_dir = str(out_dir)
        result = adapter.submit(state.output_package, state)
        assert result.success is True
        assert (out_dir / "submission_receipt.json").exists()

    def test_check_status_returns_status(self):
        from rcm_denial.services.submission_adapters import MockSubmissionAdapter
        adapter = MockSubmissionAdapter()
        status = adapter.check_status("CONF-MOCK-001", "BCBS")
        assert status.confirmation_number == "CONF-MOCK-001"
        assert status.payer_status == "received"


# ──────────────────────────────────────────────────────────────────────
# get_submission_adapter factory
# ──────────────────────────────────────────────────────────────────────

class TestAdapterFactory:
    def test_factory_returns_mock_by_default(self):
        from rcm_denial.services.submission_adapters import (
            MockSubmissionAdapter,
            get_submission_adapter,
        )
        adapter = get_submission_adapter("BCBS")
        assert isinstance(adapter, MockSubmissionAdapter)

    def test_factory_payer_registry_overrides_default(self):
        """If registry has a payer override, factory should respect it."""
        from rcm_denial.services.submission_adapters import (
            MockSubmissionAdapter,
            get_submission_adapter,
            register_payer_submission,
        )
        register_payer_submission(
            payer_id="CIGNA",
            submission_method="mock",
            payer_name="Cigna Health",
        )
        adapter = get_submission_adapter("CIGNA")
        assert isinstance(adapter, MockSubmissionAdapter)


# ──────────────────────────────────────────────────────────────────────
# Submission registry
# ──────────────────────────────────────────────────────────────────────

class TestSubmissionRegistry:
    def test_register_and_retrieve(self):
        from rcm_denial.services.submission_adapters import (
            get_payer_submission_method,
            register_payer_submission,
        )
        register_payer_submission(
            payer_id="HUMANA",
            submission_method="availity_api",
            payer_name="Humana Inc",
            portal_url="https://availity.com",
            notes="Availity direct",
        )
        row = get_payer_submission_method("HUMANA")
        assert row is not None
        assert row["submission_method"] == "availity_api"

    def test_unregistered_payer_returns_none(self):
        from rcm_denial.services.submission_adapters import get_payer_submission_method
        result = get_payer_submission_method("PAYER_NEVER_REGISTERED_XYZ")
        assert result is None

    def test_register_upserts(self):
        """Re-registering the same payer updates the row."""
        from rcm_denial.services.submission_adapters import (
            get_payer_submission_method,
            register_payer_submission,
        )
        register_payer_submission(payer_id="UPDT_PAYER", submission_method="mock")
        register_payer_submission(payer_id="UPDT_PAYER", submission_method="edi_837")
        row = get_payer_submission_method("UPDT_PAYER")
        assert row["submission_method"] == "edi_837"


# ──────────────────────────────────────────────────────────────────────
# Retry logic (tenacity)
# ──────────────────────────────────────────────────────────────────────

class TestRetryLogic:
    def test_retry_on_transient_error(self, tmp_path):
        """
        submit_approved_claim retries on transient network errors.
        We patch the adapter to fail twice then succeed.
        """
        from rcm_denial.services.review_queue import approve, enqueue_for_review
        from rcm_denial.services.submission_adapters import MockSubmissionAdapter

        state = _make_state("CLM-RETRY-001")
        enqueue_for_review(state)
        approve(state.run_id)

        call_count = {"n": 0}

        def flaky_submit(package, wf_state):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("transient network error")
            return SubmissionResult(
                success=True,
                confirmation_number="CONF-RETRY",
                payer_id="BCBS",
                submission_method="mock",
            )

        mock_adapter = MockSubmissionAdapter()
        mock_adapter.submit = flaky_submit

        with patch(
            "rcm_denial.services.submission_service.get_submission_adapter",
            return_value=mock_adapter,
        ):
            from rcm_denial.services.submission_service import submit_approved_claim
            result = submit_approved_claim(state.run_id)

        assert result.success is True
        assert call_count["n"] == 3   # failed twice, succeeded on 3rd

    def test_permanent_failure_returns_failed_result(self, tmp_path, monkeypatch):
        """After adapter errors, submit_approved_claim returns a failed SubmissionResult."""
        from rcm_denial.services.review_queue import approve, enqueue_for_review
        from rcm_denial.services.submission_adapters import MockSubmissionAdapter

        state = _make_state("CLM-RETRY-FAIL")
        enqueue_for_review(state)
        approve(state.run_id)

        def always_fail(package, wf_state):
            raise ConnectionError("always fails")

        mock_adapter = MockSubmissionAdapter()
        mock_adapter.submit = always_fail

        with patch(
            "rcm_denial.services.submission_service.get_submission_adapter",
            return_value=mock_adapter,
        ):
            from rcm_denial.services.submission_service import submit_approved_claim
            result = submit_approved_claim(state.run_id)
            assert result.success is False
