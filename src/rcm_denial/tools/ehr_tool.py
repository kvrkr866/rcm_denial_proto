##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: ehr_tool.py
# Purpose: Tool for retrieving clinical documentation from the
#          provider EHR system including encounter notes, procedure
#          details, prior auth records, and diagnosis justifications.
#          Currently uses mock data; replace with real EHR API
#          (Epic FHIR, Cerner, Athena) to integrate.
#
##########################################################

from __future__ import annotations

from datetime import date

from rcm_denial.models.claim import EhrData, EhrDocument
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


_MOCK_EHR_DATA: dict[str, dict] = {
    "PAT001": {
        "encounter_notes": [
            {
                "document_type": "encounter_note",
                "content_summary": (
                    "Patient presented with right knee pain. Examination revealed "
                    "severe osteoarthritis grade IV. Conservative treatment failed "
                    "over 6 months. Total knee arthroplasty recommended."
                ),
                "document_date": date(2024, 9, 15),
                "author": "Dr. James Wilson, MD Orthopedics",
                "is_available": True,
            }
        ],
        "procedure_details": [
            {
                "document_type": "procedure_record",
                "content_summary": (
                    "CPT 27447 - Total knee arthroplasty performed on 2024-09-20. "
                    "Cemented prosthesis implanted. No intraoperative complications."
                ),
                "document_date": date(2024, 9, 20),
                "author": "Dr. James Wilson, MD",
                "is_available": True,
            }
        ],
        "prior_auth_records": [
            {
                "document_type": "auth_record",
                "content_summary": "Prior authorization PA-2024-98765 approved for CPT 27447 on 2024-09-01.",
                "document_date": date(2024, 9, 1),
                "author": "Utilization Management",
                "is_available": True,
            }
        ],
        "diagnosis_justifications": [
            {
                "document_type": "diagnosis_justification",
                "content_summary": "X-ray confirms complete joint space narrowing. ICD-10 M17.11 - right knee OA.",
                "document_date": date(2024, 9, 10),
                "author": "Radiology",
                "is_available": True,
            }
        ],
    },
    "PAT002": {
        "encounter_notes": [
            {
                "document_type": "encounter_note",
                "content_summary": (
                    "Patient with GERD unresponsive to PPI therapy. "
                    "Upper endoscopy with biopsy ordered to rule out Barrett's esophagus."
                ),
                "document_date": date(2024, 8, 10),
                "author": "Dr. Sarah Lee, MD Gastroenterology",
                "is_available": True,
            }
        ],
        "procedure_details": [
            {
                "document_type": "procedure_record",
                "content_summary": (
                    "CPT 43239 - EGD with biopsy performed. "
                    "Biopsy samples taken from distal esophagus. Pathology pending."
                ),
                "document_date": date(2024, 8, 14),
                "author": "Dr. Sarah Lee, MD",
                "is_available": True,
            }
        ],
        "prior_auth_records": [],  # Missing auth — key scenario
        "diagnosis_justifications": [
            {
                "document_type": "diagnosis_justification",
                "content_summary": "ICD-10 K21.0 - GERD with esophagitis confirmed.",
                "document_date": date(2024, 8, 10),
                "author": "Dr. Sarah Lee, MD",
                "is_available": True,
            }
        ],
    },
    "PAT003": {
        "encounter_notes": [
            {
                "document_type": "encounter_note",
                "content_summary": (
                    "Annual wellness visit. Routine lab work ordered. "
                    "ECG performed due to patient complaint of occasional palpitations."
                ),
                "document_date": date(2024, 7, 5),
                "author": "Dr. Amy Chen, MD Internal Medicine",
                "is_available": True,
            }
        ],
        "procedure_details": [
            {
                "document_type": "procedure_record",
                "content_summary": "CPT 93000 - 12-lead ECG with interpretation. Normal sinus rhythm.",
                "document_date": date(2024, 7, 5),
                "author": "Dr. Amy Chen, MD",
                "is_available": True,
            }
        ],
        "prior_auth_records": [],
        "diagnosis_justifications": [],
    },
}


def get_ehr_records(
    provider_id: str,
    patient_id: str,
    date_of_service: date | None = None,
) -> EhrData:
    """
    Retrieves clinical documentation from the provider EHR system.

    Real integration: replace with FHIR R4 API calls:
    - GET /Encounter?patient={id}&date={dos}
    - GET /DocumentReference?patient={id}&type=encounter-note
    - GET /ClaimResponse?patient={id}

    Args:
        provider_id: The provider's NPI or internal ID.
        patient_id: The patient's identifier.
        date_of_service: Optional filter for date of service.

    Returns:
        EhrData model with all available clinical documents.

    Raises:
        ToolExecutionError: If retrieval fails.
    """
    logger.info("Retrieving EHR records", provider_id=provider_id, patient_id=patient_id)

    try:
        raw = _MOCK_EHR_DATA.get(patient_id, {})

        def build_docs(key: str) -> list[EhrDocument]:
            return [
                EhrDocument(
                    document_type=d["document_type"],
                    content_summary=d["content_summary"],
                    document_date=d.get("document_date"),
                    author=d.get("author"),
                    is_available=d.get("is_available", True),
                )
                for d in raw.get(key, [])
            ]

        ehr = EhrData(
            patient_id=patient_id,
            provider_id=provider_id,
            encounter_notes=build_docs("encounter_notes"),
            procedure_details=build_docs("procedure_details"),
            prior_auth_records=build_docs("prior_auth_records"),
            diagnosis_justifications=build_docs("diagnosis_justifications"),
        )

        logger.info(
            "EHR records retrieved",
            patient_id=patient_id,
            has_auth=ehr.has_auth_documentation,
            has_notes=ehr.has_encounter_notes,
            encounter_count=len(ehr.encounter_notes),
        )
        return ehr

    except Exception as exc:
        raise ToolExecutionError(
            f"Failed to retrieve EHR for patient {patient_id}: {exc}"
        ) from exc
