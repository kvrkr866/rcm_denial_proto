##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: patient_data_tool.py
# Purpose: Tool for retrieving patient demographics, insurance
#          coverage and eligibility. Currently uses mock data;
#          replace get_patient_from_api() to integrate with
#          real Patient Data Service / FHIR endpoint.
#
##########################################################

from __future__ import annotations

from datetime import date

from rcm_denial.models.claim import InsuranceCoverage, PatientData
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    """Raised when a tool fails to execute."""


# ------------------------------------------------------------------ #
# Mock data store (replace with real API call)
# ------------------------------------------------------------------ #

_MOCK_PATIENTS: dict[str, dict] = {
    "PAT001": {
        "first_name": "John",
        "last_name": "Smith",
        "date_of_birth": date(1975, 6, 15),
        "insurance": {
            "plan_name": "Blue Cross PPO",
            "plan_id": "BCBS-PPO-001",
            "group_number": "GRP-45678",
            "member_id": "BCB123456789",
            "effective_date": date(2024, 1, 1),
            "copay": 30.0,
            "deductible": 1500.0,
            "is_active": True,
        },
        "is_eligible": True,
    },
    "PAT002": {
        "first_name": "Maria",
        "last_name": "Garcia",
        "date_of_birth": date(1982, 3, 22),
        "insurance": {
            "plan_name": "Aetna HMO",
            "plan_id": "AET-HMO-002",
            "group_number": "GRP-99012",
            "member_id": "AET987654321",
            "effective_date": date(2023, 7, 1),
            "copay": 20.0,
            "deductible": 2000.0,
            "is_active": True,
        },
        "is_eligible": True,
    },
    "PAT003": {
        "first_name": "Robert",
        "last_name": "Johnson",
        "date_of_birth": date(1960, 11, 8),
        "insurance": {
            "plan_name": "Medicare Part B",
            "plan_id": "MCR-PARTB",
            "group_number": None,
            "member_id": "1EG4-TE5-MK72",
            "effective_date": date(2022, 1, 1),
            "copay": 0.0,
            "deductible": 240.0,
            "is_active": True,
        },
        "is_eligible": True,
        "eligibility_notes": "Medicare beneficiary — secondary payer rules may apply",
    },
}


def get_patient_data(patient_id: str) -> PatientData:
    """
    Retrieves patient demographics and insurance coverage.

    Real integration: replace with call to Patient Data Service
    (e.g. FHIR R4 /Patient/{id} + /Coverage?patient={id}).

    Args:
        patient_id: The patient's unique identifier.

    Returns:
        PatientData model with demographics and coverage.

    Raises:
        ToolExecutionError: If the patient cannot be found or retrieval fails.
    """
    logger.info("Retrieving patient data", patient_id=patient_id)

    try:
        raw = _MOCK_PATIENTS.get(patient_id)

        if raw is None:
            # Graceful fallback — return minimal record rather than failing the pipeline
            logger.warning("Patient not found in mock store — using fallback", patient_id=patient_id)
            return PatientData(
                patient_id=patient_id,
                first_name="Unknown",
                last_name="Patient",
                is_eligible=False,
                eligibility_notes="Patient record not found — manual verification required",
            )

        ins_raw = raw.get("insurance", {})
        coverage = InsuranceCoverage(
            plan_name=ins_raw.get("plan_name", "Unknown"),
            plan_id=ins_raw.get("plan_id", ""),
            group_number=ins_raw.get("group_number"),
            member_id=ins_raw.get("member_id", ""),
            effective_date=ins_raw.get("effective_date"),
            termination_date=ins_raw.get("termination_date"),
            copay=ins_raw.get("copay"),
            deductible=ins_raw.get("deductible"),
            is_active=ins_raw.get("is_active", True),
        )

        patient = PatientData(
            patient_id=patient_id,
            first_name=raw["first_name"],
            last_name=raw["last_name"],
            date_of_birth=raw.get("date_of_birth"),
            insurance_coverage=[coverage],
            is_eligible=raw.get("is_eligible", True),
            eligibility_notes=raw.get("eligibility_notes"),
        )

        logger.info("Patient data retrieved", patient_id=patient_id, eligible=patient.is_eligible)
        return patient

    except Exception as exc:
        raise ToolExecutionError(f"Failed to retrieve patient {patient_id}: {exc}") from exc
