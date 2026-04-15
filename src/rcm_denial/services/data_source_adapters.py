##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: data_source_adapters.py
# Purpose: Hook interfaces for external data sources.
#
#          Each abstract base class is a HOOK — define the contract
#          now, plug in real system clients later (Epic, Cerner,
#          Availity, Kareo, etc.) without touching any other code.
#
#          Three hook families:
#            EMRAdapter   — EHR/EMR systems (clinical records)
#            PMSAdapter   — Practice Management System (AR, eligibility)
#            PayerAdapter — Payer portals / clearinghouses (policy, appeals)
#
#          Active adapter per family is selected by settings:
#            emr_adapter   = "mock" | "epic" | "cerner" | "athena"
#            pms_adapter   = "mock" | "kareo" | "advancedmd"
#            payer_adapter = "mock" | "availity" | "change_healthcare"
#
#          Logic inside every mock adapter:
#            1. Use data already present in ClaimRecord (came from CSV) — no fetch needed.
#            2. If field is absent, return a sensible generic placeholder.
#          Real adapters will replace step 2 with actual API/EDI calls.
#
##########################################################

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import TYPE_CHECKING

from rcm_denial.models.claim import (
    DiagnosticReport,
    EhrData,
    EhrDocument,
    InsuranceCoverage,
    PatientData,
    PayerPolicy,
)
from rcm_denial.services.audit_service import get_logger

if TYPE_CHECKING:
    from rcm_denial.models.claim import ClaimRecord

logger = get_logger(__name__)


# ================================================================== #
# HOOK FAMILY 1 — EMR / EHR Adapter
# ================================================================== #

class BaseEMRAdapter(ABC):
    """
    Hook interface for EHR / EMR systems.

    Real implementations to add:
      EpicEMRAdapter     — Epic FHIR R4 REST API
      CernerEMRAdapter   — Cerner Millennium / HealtheIntent API
      AthenaEMRAdapter   — Athena Health API
      ECWEMRAdapter      — eClinicalWorks API
    """

    @abstractmethod
    def get_patient_demographics(
        self,
        patient_id: str,
        claim: ClaimRecord,
    ) -> PatientData:
        """
        Fetch patient demographics and active insurance coverage.

        FHIR equivalent:
          GET /Patient/{patient_id}
          GET /Coverage?patient={patient_id}&status=active
        """
        ...

    @abstractmethod
    def get_clinical_records(
        self,
        patient_id: str,
        provider_id: str,
        claim: ClaimRecord,
    ) -> EhrData:
        """
        Stage 1 fetch: encounter notes, procedure details, auth records.

        FHIR equivalent:
          GET /Encounter?patient={id}&date={dos}
          GET /DocumentReference?patient={id}&category=clinical-note
          GET /Procedure?patient={id}&date={dos}
          GET /ClaimResponse?patient={id}       (for auth records)
        """
        ...

    @abstractmethod
    def get_diagnostic_reports(
        self,
        patient_id: str,
        provider_id: str,
        claim: ClaimRecord,
        fetch_description: str = "",
    ) -> list[DiagnosticReport]:
        """
        Gap 14 — Stage 2 targeted fetch: lab results, imaging, pathology.

        Called only when evidence_check_agent sets needs_additional_ehr_fetch=True.
        fetch_description narrows the query (e.g. "MRI brain ordered 2024-01-15").

        FHIR equivalent:
          GET /DiagnosticReport?patient={id}&date={dos}
          GET /Observation?patient={id}&category=laboratory
          GET /ImagingStudy?patient={id}&started={dos}
        """
        ...


class MockEMRAdapter(BaseEMRAdapter):
    """
    Mock EMR adapter for development and testing.

    Uses ClaimRecord fields populated from the CSV when available.
    Falls back to generic placeholder data when fields are absent.
    Real EMR adapters will replace the fallback path with actual API calls.
    """

    def get_patient_demographics(
        self,
        patient_id: str,
        claim: ClaimRecord,
    ) -> PatientData:
        logger.debug("MockEMRAdapter.get_patient_demographics", patient_id=patient_id)

        # ---- Use CSV data when present ----
        first_name, last_name = "Unknown", "Patient"
        if claim.patient_name:
            parts = claim.patient_name.strip().split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""

        coverage = None
        member_id = claim.member_id or claim.patient_id
        payer_name = claim.payer_name or claim.payer_id
        if payer_name:
            coverage = InsuranceCoverage(
                plan_name=payer_name,
                plan_id=claim.payer_id,
                member_id=member_id,
                is_active=True,
            )

        return PatientData(
            patient_id=patient_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=getattr(claim, "patient_dob", None),
            insurance_coverage=[coverage] if coverage else [],
            is_eligible=True,
            eligibility_notes=(
                "Data sourced from CSV intake record"
                if claim.patient_name
                else "Patient demographics not in CSV — real EMR fetch required"
            ),
        )

    def get_clinical_records(
        self,
        patient_id: str,
        provider_id: str,
        claim: ClaimRecord,
    ) -> EhrData:
        logger.debug("MockEMRAdapter.get_clinical_records", patient_id=patient_id)

        dos = claim.date_of_service
        cpt_list = ", ".join(claim.cpt_codes) if claim.cpt_codes else "unspecified"
        dx_list = ", ".join(claim.diagnosis_codes) if claim.diagnosis_codes else "unspecified"
        specialty = getattr(claim, "specialty", None) or "the ordering provider"

        encounter_notes = [
            EhrDocument(
                document_type="encounter_note",
                content_summary=(
                    f"Patient presented to {specialty}. "
                    f"Relevant diagnosis: {dx_list}. "
                    f"Procedure(s) ordered: {cpt_list}. "
                    f"Medical necessity documented per treating physician."
                ),
                document_date=dos,
                author=f"Treating Provider (NPI: {provider_id})",
                is_available=True,
            )
        ]

        procedure_details = [
            EhrDocument(
                document_type="procedure_record",
                content_summary=(
                    f"CPT {cpt_list} performed on {dos}. "
                    f"Diagnosis: {dx_list}. "
                    f"No documented complications."
                ),
                document_date=dos,
                author=f"Provider (NPI: {provider_id})",
                is_available=True,
            )
        ]

        # Auth record: include only if claim indicates auth was required
        prior_auth_records: list[EhrDocument] = []
        if getattr(claim, "requires_auth", False):
            prior_auth_records.append(
                EhrDocument(
                    document_type="auth_record",
                    content_summary=(
                        f"Prior authorization on file for {cpt_list}. "
                        f"Verify auth number against payer records — "
                        f"real auth document requires EMR fetch."
                    ),
                    document_date=dos - timedelta(days=14) if dos else None,
                    author="Utilization Management",
                    is_available=False,   # not truly attached until real EMR call
                )
            )

        return EhrData(
            patient_id=patient_id,
            provider_id=provider_id,
            encounter_notes=encounter_notes,
            procedure_details=procedure_details,
            prior_auth_records=prior_auth_records,
            diagnosis_justifications=[],
        )

    def get_diagnostic_reports(
        self,
        patient_id: str,
        provider_id: str,
        claim: ClaimRecord,
        fetch_description: str = "",
    ) -> list[DiagnosticReport]:
        """
        Mock Stage 2: returns a placeholder diagnostic report.
        Real adapter will query FHIR DiagnosticReport / ImagingStudy / Observation.
        """
        logger.debug("MockEMRAdapter.get_diagnostic_reports", patient_id=patient_id)
        dos = claim.date_of_service
        cpt_list = ", ".join(claim.cpt_codes) if claim.cpt_codes else "unspecified"

        # Infer category from the fetch_description hint when possible
        cat = "lab"
        name = "Diagnostic Report"
        if fetch_description:
            desc_lower = fetch_description.lower()
            if any(w in desc_lower for w in ("mri", "ct", "imaging", "x-ray", "ultrasound")):
                cat, name = "imaging", "Imaging Study"
            elif any(w in desc_lower for w in ("pathology", "biopsy", "histology")):
                cat, name = "pathology", "Pathology Report"
            elif any(w in desc_lower for w in ("lab", "blood", "cbc", "panel", "culture")):
                cat, name = "lab", "Laboratory Results"

        return [
            DiagnosticReport(
                report_id=f"MOCK-DR-{claim.claim_id}",
                report_category=cat,
                report_name=name,
                report_date=dos,
                ordering_provider=f"Provider (NPI: {provider_id})",
                conclusion=(
                    f"Findings support medical necessity for CPT {cpt_list}. "
                    "Results within expected range for clinical presentation."
                ),
                key_findings=[
                    "Clinical findings consistent with documented diagnosis",
                    f"Procedure(s) {cpt_list} clinically indicated",
                ],
                is_available=True,
                source="mock",
                content_summary=(
                    f"Mock {cat} report for {claim.claim_id}. "
                    f"Fetch description: {fetch_description or 'none'}. "
                    "Real report requires Stage 2 EMR fetch."
                ),
            )
        ]


# ------------------------------------------------------------------ #
# Gap 15 — RPAEMRAdapter scaffold
# Playwright/Selenium-based portal scraping for EHRs without REST APIs.
# ------------------------------------------------------------------ #

class RPAEMRAdapter(BaseEMRAdapter):
    """
    Scaffold for RPA-based EHR access using Playwright (preferred) or Selenium.

    Use when the provider's EHR does not expose a REST/FHIR API and records
    must be retrieved by automating the provider web portal.

    To activate:
      1. Install: pip install playwright && playwright install chromium
      2. Set EMR_ADAPTER=rpa_portal in .env
      3. Implement the _login(), _navigate_to_patient(), and _extract_*()
         methods below for the specific portal being automated.
      4. Store portal credentials in .env / secrets manager; reference
         via credentials_ref in medical_record_source_registry.
    """

    def __init__(self, portal_url: str = "", credentials_ref: str = "") -> None:
        self.portal_url = portal_url
        self.credentials_ref = credentials_ref
        self._browser = None   # Playwright Browser instance (lazy init)
        self._page = None      # Playwright Page instance

    # ---- Internal RPA lifecycle (stub — implement per portal) ----

    def _launch_browser(self) -> None:
        """Launch headless Chromium. Called once per batch session."""
        try:
            from playwright.sync_api import sync_playwright   # type: ignore[import]
            self._pw = sync_playwright().__enter__()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            logger.info("RPAEMRAdapter browser launched")
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

    def _login(self) -> None:
        """Authenticate to the EHR portal. Implement per portal."""
        raise NotImplementedError("RPAEMRAdapter._login() must be implemented for target portal")

    def _close_browser(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
            self._page = None
            logger.info("RPAEMRAdapter browser closed")

    # ---- BaseEMRAdapter interface ----

    def get_patient_demographics(
        self,
        patient_id: str,
        claim: ClaimRecord,
    ) -> PatientData:
        """
        Stub: navigate to patient search, scrape demographics.
        Implement _navigate_to_patient() and _extract_demographics() per portal.
        """
        raise NotImplementedError(
            "RPAEMRAdapter.get_patient_demographics() not yet implemented for this portal"
        )

    def get_clinical_records(
        self,
        patient_id: str,
        provider_id: str,
        claim: ClaimRecord,
    ) -> EhrData:
        """
        Stub: navigate to clinical notes section, scrape encounter notes.
        Implement _navigate_to_clinical_notes() and _extract_notes() per portal.
        """
        raise NotImplementedError(
            "RPAEMRAdapter.get_clinical_records() not yet implemented for this portal"
        )

    def get_diagnostic_reports(
        self,
        patient_id: str,
        provider_id: str,
        claim: ClaimRecord,
        fetch_description: str = "",
    ) -> list[DiagnosticReport]:
        """
        Stub: navigate to results/reports section, download and OCR report PDFs.
        Use clinical_ocr_tool to extract text from downloaded PDFs.
        """
        raise NotImplementedError(
            "RPAEMRAdapter.get_diagnostic_reports() not yet implemented for this portal"
        )


# ================================================================== #
# HOOK FAMILY 2 — PMS Adapter (Practice Management System)
# ================================================================== #

class BasePMSAdapter(ABC):
    """
    Hook interface for Practice Management Systems.

    Real implementations to add:
      KareoPMSAdapter       — Kareo / Tebra API
      AdvancedMDAdapter     — AdvancedMD REST API
      DrChronoAdapter       — DrChrono API
      AthenaCollectAdapter  — Athena Collector (billing module)
    """

    @abstractmethod
    def get_claim_history(
        self,
        claim_id: str,
        patient_id: str,
        claim: ClaimRecord,
    ) -> dict:
        """
        Fetch prior submissions, remittance history, and AR aging for this claim.

        Typical PMS query:
          GET /claims/{claim_id}/history
          GET /patients/{patient_id}/ar
        """
        ...

    @abstractmethod
    def get_eligibility(
        self,
        patient_id: str,
        payer_id: str,
        date_of_service: date,
    ) -> dict:
        """
        Real-time eligibility check (270/271 EDI transaction).

        Clearinghouse equivalent:
          POST /eligibility   with NPI + member_id + dos
        """
        ...


class MockPMSAdapter(BasePMSAdapter):
    """
    Mock PMS adapter — uses ClaimRecord AR fields from CSV when available.
    """

    def get_claim_history(
        self,
        claim_id: str,
        patient_id: str,
        claim: ClaimRecord,
    ) -> dict:
        logger.debug("MockPMSAdapter.get_claim_history", claim_id=claim_id)

        prior_attempts = getattr(claim, "prior_appeal_attempts", 0) or 0
        days_in_ar = getattr(claim, "days_in_ar", None)

        return {
            "claim_id": claim_id,
            "patient_id": patient_id,
            "original_submission_date": str(claim.date_of_service),
            "denial_date": str(claim.denial_date),
            "prior_appeal_attempts": prior_attempts,
            "days_in_ar": days_in_ar,
            "billed_amount": claim.billed_amount,
            "paid_amount": getattr(claim, "paid_amount", None),
            "contracted_rate": getattr(claim, "contracted_rate", None),
            "source": "csv_intake" if days_in_ar is not None else "mock_placeholder",
            "note": (
                "AR data sourced from CSV"
                if days_in_ar is not None
                else "AR history not in CSV — real PMS fetch required"
            ),
        }

    def get_eligibility(
        self,
        patient_id: str,
        payer_id: str,
        date_of_service: date,
    ) -> dict:
        logger.debug("MockPMSAdapter.get_eligibility", patient_id=patient_id, payer_id=payer_id)

        return {
            "patient_id": patient_id,
            "payer_id": payer_id,
            "date_of_service": str(date_of_service),
            "is_eligible": True,
            "coverage_active": True,
            "source": "mock_placeholder",
            "note": "Eligibility not verified — real 270/271 EDI check required",
        }


# ================================================================== #
# HOOK FAMILY 3 — Payer Adapter
# ================================================================== #

class BasePayerAdapter(ABC):
    """
    Hook interface for payer portals and clearinghouses.

    Real implementations to add:
      AvailityPayerAdapter          — Availity REST API
      ChangeHealthcareAdapter       — Change Healthcare (Optum) API
      NaviNetAdapter                — NaviNet portal integration
      EDI835Adapter                 — Parse 835 remittance files directly
    """

    @abstractmethod
    def get_policy(
        self,
        payer_id: str,
        cpt_codes: list[str],
        claim: ClaimRecord,
    ) -> PayerPolicy:
        """
        Fetch payer coverage rules, filing limits, and appeal instructions.

        Payer portal / clearinghouse equivalent:
          GET /payers/{payer_id}/policies
          GET /payers/{payer_id}/timely-filing
        """
        ...

    @abstractmethod
    def get_appeal_instructions(
        self,
        payer_id: str,
        carc_code: str,
    ) -> dict:
        """
        Fetch payer-specific appeal steps for a given denial reason (CARC).

        Some payers publish CARC-specific appeal paths via their portal APIs.
        """
        ...


class MockPayerAdapter(BasePayerAdapter):
    """
    Mock payer adapter — uses ClaimRecord payer fields from CSV when available.
    Falls back to generic policy defaults for unknown payers.
    """

    # Generic billing guidelines that apply to most commercial payers
    _GENERIC_GUIDELINES = [
        "Submit corrected claims within the payer's timely filing window",
        "Attach supporting clinical documentation for medical necessity appeals",
        "Reference the denial CARC/RARC codes in your appeal letter",
        "Include the original EOB with your appeal submission",
    ]

    # CARC-specific appeal guidance
    _CARC_APPEAL_HINTS: dict[str, str] = {
        "4":  "Verify service dates and resubmit with corrected DOS",
        "11": "Confirm this is not covered under a different payer — COB review required",
        "16": "Missing or incomplete claim data — identify and correct the missing field",
        "18": "Duplicate claim — verify original claim status before resubmitting",
        "22": "Coordination of benefits — attach primary payer EOB",
        "29": "Timely filing exceeded — submit proof of timely filing if available",
        "50": "Medical necessity — attach clinical notes and physician letter",
        "96": "Non-covered charge — review plan exclusions; consider patient responsibility",
        "97": "Prior authorization required — attach auth approval or request retro-auth",
        "119": "Benefit maximum reached — verify accumulator and document medical necessity",
        "167": "Out-of-network — verify credentialing and check gap exception eligibility",
        "252": "Missing authorization — attach auth number or request retrospective authorization",
    }

    def get_policy(
        self,
        payer_id: str,
        cpt_codes: list[str],
        claim: ClaimRecord,
    ) -> PayerPolicy:
        logger.debug("MockPayerAdapter.get_policy", payer_id=payer_id)

        # ---- Use CSV payer data when present ----
        payer_name = getattr(claim, "payer_name", None) or f"Payer ({payer_id})"
        portal_url = getattr(claim, "payer_portal_url", None)
        filing_days = getattr(claim, "payer_filing_deadline_days", None) or 365

        # Appeal deadline: prefer claim-level data, fall back to standard 180 days
        appeal_days = 180
        if getattr(claim, "appeal_deadline", None) and getattr(claim, "days_to_deadline", None):
            # Back-calculate from days_to_deadline + today
            appeal_days = int(
                (claim.appeal_deadline - claim.date_of_service).days
            ) if claim.appeal_deadline and claim.date_of_service else 180

        notes = [
            f"Payer portal: {portal_url}" if portal_url else
            "Payer portal URL not in CSV — verify via payer adapter",
            f"Phone: {claim.payer_phone}" if getattr(claim, "payer_phone", None) else
            "Payer phone not in CSV",
        ]

        return PayerPolicy(
            payer_id=payer_id,
            payer_name=payer_name,
            billing_guidelines=self._GENERIC_GUIDELINES,
            covered_cpt_codes=cpt_codes,          # assume billed codes are covered unless told otherwise
            prior_auth_required_codes=(
                cpt_codes if getattr(claim, "requires_auth", False) else []
            ),
            timely_filing_limit_days=filing_days,
            appeal_deadline_days=appeal_days,
            appeal_portal_url=portal_url,
            appeal_address=None,                   # real adapter fetches this
            appeal_fax=None,                       # real adapter fetches this
            notes=notes,
        )

    def get_appeal_instructions(
        self,
        payer_id: str,
        carc_code: str,
    ) -> dict:
        logger.debug("MockPayerAdapter.get_appeal_instructions", payer_id=payer_id, carc_code=carc_code)

        hint = self._CARC_APPEAL_HINTS.get(
            str(carc_code),
            "Review denial reason and attach supporting documentation",
        )
        return {
            "payer_id": payer_id,
            "carc_code": carc_code,
            "appeal_steps": [
                hint,
                "Complete payer's standard appeal form",
                "Submit via payer portal or certified mail within the appeal deadline",
            ],
            "source": "mock_carc_hints",
            "note": "Real payer-specific instructions require payer adapter integration",
        }


# ================================================================== #
# Gap 36 — Batch-level EHR session manager
#
# One adapter instance is shared for the entire batch.
# batch_processor calls initialize_ehr_session() before the loop and
# close_ehr_session() after.  Individual agents call get_ehr_session()
# which always returns the cached instance (no reconnect overhead).
# ================================================================== #

_ehr_sessions: dict[str, BaseEMRAdapter] = {}  # batch_id → adapter


def initialize_ehr_session(batch_id: str, adapter_type: str = "mock") -> BaseEMRAdapter:
    """
    Creates (or reuses) the EMR adapter for this batch.

    For RPA adapters this is where the browser is launched and the portal
    login happens — once per batch, not once per claim.

    Args:
        batch_id:     Unique identifier for the current batch run.
        adapter_type: Adapter key from settings.emr_adapter.

    Returns:
        The active EMR adapter for this batch.
    """
    if batch_id in _ehr_sessions:
        logger.debug("EHR session already open", batch_id=batch_id)
        return _ehr_sessions[batch_id]

    adapter = get_emr_adapter(adapter_type)

    # For RPA adapter: launch browser + login now (once per batch)
    if isinstance(adapter, RPAEMRAdapter):
        adapter._launch_browser()
        adapter._login()

    _ehr_sessions[batch_id] = adapter
    logger.info("EHR session initialized", batch_id=batch_id, adapter_type=adapter_type)
    return adapter


def get_ehr_session(batch_id: str) -> BaseEMRAdapter | None:
    """
    Returns the cached EHR adapter for this batch, or None if not initialized.
    Falls back to a fresh mock adapter when no session exists (single-claim calls).
    """
    return _ehr_sessions.get(batch_id)


def close_ehr_session(batch_id: str) -> None:
    """
    Releases resources for this batch's EHR session.
    For RPA adapters: closes the browser.
    """
    adapter = _ehr_sessions.pop(batch_id, None)
    if adapter is None:
        return
    if isinstance(adapter, RPAEMRAdapter):
        adapter._close_browser()
    logger.info("EHR session closed", batch_id=batch_id)


# ================================================================== #
# Adapter factories — controlled by settings.emr/pms/payer_adapter
# ================================================================== #

def get_emr_adapter(adapter_type: str = "mock") -> BaseEMRAdapter:
    """
    Returns a new EMR adapter instance.

    Prefer initialize_ehr_session() for batch processing to avoid
    creating a new adapter (and browser session) per claim.

    To add a real adapter:
      1. Create EpicEMRAdapter(BaseEMRAdapter) in a new file
      2. Add elif adapter_type == "epic": return EpicEMRAdapter()
      3. Set EMR_ADAPTER=epic in .env
    """
    if adapter_type == "mock":
        return MockEMRAdapter()
    if adapter_type == "rpa_portal":
        return RPAEMRAdapter()
    raise NotImplementedError(
        f"EMR adapter '{adapter_type}' not implemented. "
        f"Create a subclass of BaseEMRAdapter and register it here."
    )


def get_pms_adapter(adapter_type: str = "mock") -> BasePMSAdapter:
    """
    Returns the configured PMS adapter.

    To add a real adapter:
      1. Create KareoPMSAdapter(BasePMSAdapter)
      2. Add elif adapter_type == "kareo": return KareoPMSAdapter()
      3. Set PMS_ADAPTER=kareo in .env
    """
    if adapter_type == "mock":
        return MockPMSAdapter()
    raise NotImplementedError(
        f"PMS adapter '{adapter_type}' not implemented. "
        f"Create a subclass of BasePMSAdapter and register it here."
    )


def get_payer_adapter(adapter_type: str = "mock") -> BasePayerAdapter:
    """
    Returns the configured payer adapter.

    To add a real adapter:
      1. Create AvailityPayerAdapter(BasePayerAdapter)
      2. Add elif adapter_type == "availity": return AvailityPayerAdapter()
      3. Set PAYER_ADAPTER=availity in .env
    """
    if adapter_type == "mock":
        return MockPayerAdapter()
    raise NotImplementedError(
        f"Payer adapter '{adapter_type}' not implemented. "
        f"Create a subclass of BasePayerAdapter and register it here."
    )
