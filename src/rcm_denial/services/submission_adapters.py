##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: submission_adapters.py
# Purpose: Phase 5 — Payer submission adapter hooks.
#
#          Gap 20: BaseSubmissionAdapter — abstract interface
#          Gap 21: MockSubmissionAdapter — dev/test; writes to output dir
#          Gap 22: AvailitySubmissionAdapter scaffold — REST API
#          Gap 23: RPASubmissionAdapter scaffold — Playwright portal
#          Gap 24: EDI837SubmissionAdapter scaffold — EDI 837P/I transaction
#
#          Active adapter per payer is resolved by:
#            1. payer_submission_registry table (per-payer override)
#            2. settings.submission_adapter (global default)
#
#          Registry helpers:
#            register_payer_submission()  — onboard a new payer
#            get_payer_submission_method() — look up method for a payer
#
##########################################################

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rcm_denial.models.submission import SubmissionResult, SubmissionStatus
from rcm_denial.services.audit_service import get_logger

if TYPE_CHECKING:
    from rcm_denial.models.output import DenialWorkflowState, SubmissionPackage

logger = get_logger(__name__)


# ================================================================== #
# Gap 20 — BaseSubmissionAdapter
# ================================================================== #

class BaseSubmissionAdapter(ABC):
    """
    Abstract hook interface for payer claim / appeal submission.

    Real implementations:
      AvailitySubmissionAdapter   — Availity REST API (most commercial payers)
      ChangeHealthcareAdapter     — Change Healthcare / Optum 360 API
      RPASubmissionAdapter        — Playwright browser automation for portal
      EDI837SubmissionAdapter     — Raw EDI 837P/I transaction via clearinghouse
      MailSubmissionAdapter       — Generate mailing instructions + certified mail slip
    """

    @abstractmethod
    def submit(
        self,
        package: "SubmissionPackage",
        state: "DenialWorkflowState",
    ) -> SubmissionResult:
        """
        Submits the denial response package to the payer.

        Args:
            package: Final SubmissionPackage with PDF path and metadata.
            state:   Full workflow state (claim details, payer info, etc.).

        Returns:
            SubmissionResult with success flag and confirmation number.
        """
        ...

    @abstractmethod
    def check_status(self, confirmation_number: str, payer_id: str) -> SubmissionStatus:
        """
        Checks the adjudication status of a previously submitted claim.

        Args:
            confirmation_number: Returned by submit() on success.
            payer_id:            Payer identifier for routing.

        Returns:
            SubmissionStatus with current payer status.
        """
        ...


# ================================================================== #
# Gap 21 — MockSubmissionAdapter
# ================================================================== #

class MockSubmissionAdapter(BaseSubmissionAdapter):
    """
    Mock submission adapter for development and testing.

    Simulates a successful submission by:
      1. Writing a submission_receipt.json to the claim's output directory.
      2. Returning a deterministic confirmation number.

    No network calls made. Safe for offline/CI environments.
    """

    def submit(
        self,
        package: "SubmissionPackage",
        state: "DenialWorkflowState",
    ) -> SubmissionResult:
        logger.info(
            "MockSubmissionAdapter.submit",
            claim_id=package.claim_id,
            package_type=package.package_type,
        )

        confirmation_number = f"MOCK-{package.claim_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        # Write receipt to output dir
        if package.output_dir:
            receipt_path = Path(package.output_dir) / "submission_receipt.json"
            receipt = {
                "claim_id":            package.claim_id,
                "run_id":              package.run_id,
                "payer_id":            state.claim.payer_id,
                "package_type":        package.package_type,
                "confirmation_number": confirmation_number,
                "submitted_at":        datetime.utcnow().isoformat(),
                "method":              "mock",
                "note":                "Mock submission — no actual payer contact made",
            }
            try:
                with open(receipt_path, "w") as f:
                    json.dump(receipt, f, indent=2)
            except Exception as exc:
                logger.warning("Could not write receipt file", error=str(exc))

        return SubmissionResult(
            success=True,
            submission_method="mock",
            confirmation_number=confirmation_number,
            response_code="200",
            response_message="Mock submission accepted",
            submitted_at=datetime.utcnow(),
            response_received_at=datetime.utcnow(),
        )

    def check_status(self, confirmation_number: str, payer_id: str) -> SubmissionStatus:
        return SubmissionStatus(
            confirmation_number=confirmation_number,
            payer_status="received",
            payer_notes="Mock status — real payer check not performed",
        )


# ================================================================== #
# Gap 22 — AvailitySubmissionAdapter scaffold
# ================================================================== #

class AvailitySubmissionAdapter(BaseSubmissionAdapter):
    """
    Scaffold for Availity REST API submission.

    Availity is the largest multi-payer clearinghouse — supports BCBS, Aetna,
    Cigna, Humana, UHC, and many regional payers via a single API.

    To activate:
      1. Register at availity.com/developer
      2. Set AVAILITY_CLIENT_ID and AVAILITY_CLIENT_SECRET in .env
      3. Set submission_method = 'availity_api' in payer_submission_registry
      4. Implement _get_access_token() and the POST /claims endpoint call below

    API reference: https://developer.availity.com/partner/documentation
    """

    def __init__(
        self,
        api_endpoint: str = "https://api.availity.com/availity/v1",
        credentials_ref: str = "AVAILITY",
    ) -> None:
        self.api_endpoint = api_endpoint
        self.credentials_ref = credentials_ref
        self._token: str | None = None

    def _get_access_token(self) -> str:
        """
        Obtain OAuth2 bearer token from Availity.
        Implement with: POST /availity/v1/token using client_credentials flow.
        """
        raise NotImplementedError(
            "Implement _get_access_token() using Availity OAuth2 client_credentials flow. "
            "Store client_id/secret in .env as AVAILITY_CLIENT_ID / AVAILITY_CLIENT_SECRET."
        )

    def _build_claim_payload(self, package, state) -> dict:
        """
        Build the Availity claim submission payload (837P JSON format).
        Map DenialWorkflowState fields to Availity API fields.
        """
        raise NotImplementedError(
            "Implement _build_claim_payload() mapping ClaimRecord fields "
            "to the Availity 837P JSON submission schema."
        )

    def submit(self, package, state) -> SubmissionResult:
        """
        Stub: POST to Availity /claims endpoint with 837P payload.
        Attach PDF package as multipart/form-data if required.
        """
        raise NotImplementedError(
            "AvailitySubmissionAdapter.submit() not yet implemented. "
            "Implement _get_access_token(), _build_claim_payload(), "
            "and the POST /claims API call."
        )

    def check_status(self, confirmation_number: str, payer_id: str) -> SubmissionStatus:
        """Stub: GET /claims/{confirmation_number}/status"""
        raise NotImplementedError(
            "AvailitySubmissionAdapter.check_status() not yet implemented."
        )


# ================================================================== #
# Gap 23 — RPASubmissionAdapter scaffold
# ================================================================== #

class RPASubmissionAdapter(BaseSubmissionAdapter):
    """
    Scaffold for RPA-based payer portal submission via Playwright.

    Use when the payer does not support API submission and requires
    manual portal login + form fill. Playwright automates this.

    To activate:
      1. pip install playwright && playwright install chromium
      2. Set submission_method = 'rpa_portal' in payer_submission_registry
      3. Set portal_url and credentials_ref in payer_submission_registry
      4. Implement _login(), _navigate_to_appeal_form(), _fill_form(),
         _upload_pdf(), _submit_form() for the target payer portal

    Portal-specific implementations should subclass RPASubmissionAdapter.
    """

    def __init__(self, portal_url: str = "", credentials_ref: str = "") -> None:
        self.portal_url = portal_url
        self.credentials_ref = credentials_ref
        self._browser = None
        self._page = None

    def _launch_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright   # type: ignore[import]
            self._pw = sync_playwright().__enter__()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

    def _close_browser(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
            self._page = None

    def _login(self) -> None:
        """Authenticate to payer portal. Implement per portal."""
        raise NotImplementedError("RPASubmissionAdapter._login() must be implemented per portal")

    def _navigate_to_appeal_form(self, claim_id: str) -> None:
        """Navigate to the claim appeal / corrected claim submission form."""
        raise NotImplementedError("Implement _navigate_to_appeal_form() for target portal")

    def _upload_pdf(self, pdf_path: str) -> None:
        """Upload the submission package PDF via portal file upload widget."""
        raise NotImplementedError("Implement _upload_pdf() for target portal")

    def _submit_form(self) -> str:
        """Submit the form and return the confirmation number shown on screen."""
        raise NotImplementedError("Implement _submit_form() for target portal")

    def submit(self, package, state) -> SubmissionResult:
        """
        Stub: launch browser, login, navigate, upload PDF, submit form.
        """
        raise NotImplementedError(
            "RPASubmissionAdapter.submit() not yet implemented. "
            "Implement the portal-specific methods above."
        )

    def check_status(self, confirmation_number: str, payer_id: str) -> SubmissionStatus:
        """Stub: login and navigate to claim status page."""
        raise NotImplementedError(
            "RPASubmissionAdapter.check_status() not yet implemented."
        )


# ================================================================== #
# Gap 24 — EDI837SubmissionAdapter scaffold
# ================================================================== #

class EDI837SubmissionAdapter(BaseSubmissionAdapter):
    """
    Scaffold for EDI 837P/I claim submission via a clearinghouse.

    EDI 837P = Professional claim (physician / outpatient)
    EDI 837I = Institutional claim (hospital / inpatient)

    Typical clearinghouses: Change Healthcare, Waystar, Trizetto, Apex EDI.

    To activate:
      1. Obtain ISA/GS segment IDs from your clearinghouse
      2. Set CLEARINGHOUSE_SFTP_HOST, _USER, _KEY_PATH in .env
      3. Set submission_method = 'edi_837' in payer_submission_registry
      4. Set clearinghouse_id and credentials_ref in registry
      5. Implement _build_837_transaction(), _transmit_via_sftp()

    For corrected claim resubmissions: frequency code = 7 (replacement).
    For appeals: attach 837 + cover letter as agreed with clearinghouse.
    """

    def __init__(
        self,
        clearinghouse_id: str = "",
        credentials_ref: str = "CLEARINGHOUSE",
        claim_type: str = "837P",   # "837P" or "837I"
    ) -> None:
        self.clearinghouse_id = clearinghouse_id
        self.credentials_ref = credentials_ref
        self.claim_type = claim_type

    def _build_837_transaction(self, package, state) -> str:
        """
        Build the EDI 837 transaction set string.
        Map ClaimRecord fields to X12 837P/I segments:
          ISA — Interchange control header
          GS  — Functional group header
          ST  — Transaction set header (837)
          BHT — Beginning of hierarchical transaction
          NM1 — Entity names (subscriber, provider, payer)
          CLM — Claim information
          SV1 — Professional service (837P) / UB04 (837I)
          DTP — Service dates
          SE  — Transaction set trailer
          GE/IEA — Functional/interchange trailers
        """
        raise NotImplementedError(
            "Implement _build_837_transaction() mapping ClaimRecord → X12 837P/I segments. "
            "Use python-x12 or pyx12 library for segment generation."
        )

    def _transmit_via_sftp(self, edi_content: str, filename: str) -> str:
        """
        Transmit EDI file to clearinghouse SFTP drop folder.
        Returns the ISA control number for tracking.
        """
        raise NotImplementedError(
            "Implement _transmit_via_sftp() using paramiko or fabric. "
            "SFTP credentials: CLEARINGHOUSE_SFTP_HOST / _USER / _KEY_PATH in .env."
        )

    def submit(self, package, state) -> SubmissionResult:
        """Stub: build 837 transaction and transmit via SFTP."""
        raise NotImplementedError(
            "EDI837SubmissionAdapter.submit() not yet implemented. "
            "Implement _build_837_transaction() and _transmit_via_sftp()."
        )

    def check_status(self, confirmation_number: str, payer_id: str) -> SubmissionStatus:
        """Stub: retrieve 277 claim status transaction from clearinghouse."""
        raise NotImplementedError(
            "EDI837SubmissionAdapter.check_status() not yet implemented. "
            "Retrieve 277 (Claim Status Response) from clearinghouse SFTP."
        )


# ================================================================== #
# Gap 19 — Payer submission registry helpers
# ================================================================== #

def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def register_payer_submission(
    *,
    payer_id: str,
    payer_name: str = "",
    submission_method: str = "mock",
    api_endpoint: str = "",
    credentials_ref: str = "",
    portal_url: str = "",
    clearinghouse_id: str = "",
    notes: str = "",
) -> None:
    """
    Upserts a payer's submission configuration in the registry.

    submission_method values:
      'mock'                — dev/test only
      'availity_api'        — Availity multi-payer REST API
      'change_healthcare_api' — Change Healthcare / Optum 360
      'rpa_portal'          — Playwright browser portal automation
      'edi_837'             — EDI 837P/I via clearinghouse SFTP
      'mail'                — Physical mail (generate cover + certified mail slip)

    Call this once per payer during onboarding.  The submission_service
    reads this registry to select the right adapter automatically.
    """
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    with sqlite3.connect(_get_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO payer_submission_registry
                (payer_id, payer_name, submission_method,
                 api_endpoint, credentials_ref, portal_url,
                 clearinghouse_id, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(payer_id) DO UPDATE SET
                payer_name         = excluded.payer_name,
                submission_method  = excluded.submission_method,
                api_endpoint       = excluded.api_endpoint,
                credentials_ref    = excluded.credentials_ref,
                portal_url         = excluded.portal_url,
                clearinghouse_id   = excluded.clearinghouse_id,
                notes              = excluded.notes,
                updated_at         = CURRENT_TIMESTAMP
            """,
            (payer_id, payer_name, submission_method,
             api_endpoint, credentials_ref, portal_url,
             clearinghouse_id, notes),
        )
        conn.commit()

    logger.info(
        "Payer submission method registered",
        payer_id=payer_id,
        method=submission_method,
    )


def get_payer_submission_method(payer_id: str) -> dict | None:
    """Returns the registry row for a payer, or None if not registered."""
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM payer_submission_registry WHERE payer_id = ? AND is_active = 1",
            (payer_id,),
        ).fetchone()
    return dict(row) if row else None


def list_payer_submission_registry() -> list[dict]:
    """Returns all registered payers with their submission methods."""
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM payer_submission_registry ORDER BY payer_id"
        ).fetchall()
    return [dict(r) for r in rows]


# ================================================================== #
# Adapter factory — resolves per-payer method then global default
# ================================================================== #

def get_submission_adapter(payer_id: str) -> BaseSubmissionAdapter:
    """
    Returns the correct submission adapter for a payer.

    Resolution order:
      1. payer_submission_registry table (per-payer override)
      2. settings.submission_adapter (global default)

    To add a real adapter:
      1. Implement EpicSubmissionAdapter(BaseSubmissionAdapter)
      2. Register payer: register_payer_submission(payer_id='BCBS', method='availity_api')
      3. Add elif branch below
    """
    from rcm_denial.config.settings import settings

    registry_row = get_payer_submission_method(payer_id)
    method = registry_row["submission_method"] if registry_row else settings.submission_adapter

    portal_url    = (registry_row or {}).get("portal_url", "")
    api_endpoint  = (registry_row or {}).get("api_endpoint", "")
    credentials   = (registry_row or {}).get("credentials_ref", "")
    clearinghouse = (registry_row or {}).get("clearinghouse_id", "")

    logger.debug(
        "Submission adapter resolved",
        payer_id=payer_id,
        method=method,
    )

    if method == "mock":
        return MockSubmissionAdapter()
    if method == "availity_api":
        return AvailitySubmissionAdapter(
            api_endpoint=api_endpoint or "https://api.availity.com/availity/v1",
            credentials_ref=credentials,
        )
    if method == "rpa_portal":
        return RPASubmissionAdapter(
            portal_url=portal_url,
            credentials_ref=credentials,
        )
    if method == "edi_837":
        return EDI837SubmissionAdapter(
            clearinghouse_id=clearinghouse,
            credentials_ref=credentials,
        )

    logger.warning(
        "Unknown submission method — falling back to mock",
        payer_id=payer_id,
        method=method,
    )
    return MockSubmissionAdapter()
