##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: ehr_tool.py
# Purpose: Retrieves clinical documentation (encounter notes,
#          procedures, prior auth, diagnosis justification) via
#          the configured EMR adapter hook.
#
#          Active adapter is set by settings.emr_adapter:
#            "mock"    — generates contextual notes from ClaimRecord fields
#            "epic"    — Epic FHIR R4 DocumentReference / Encounter
#            "cerner"  — Cerner Millennium clinical document API
#            "athena"  — Athena Health clinical records API
#
##########################################################

from __future__ import annotations

from datetime import date

from rcm_denial.models.claim import ClaimRecord, EhrData
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


def get_ehr_records(
    provider_id: str,
    patient_id: str,
    date_of_service: date | None = None,
    claim: ClaimRecord | None = None,
) -> EhrData:
    """
    Retrieves clinical documentation from the provider EHR system.

    Delegates to the EMR adapter configured in settings.emr_adapter.
    The mock adapter builds contextual notes from ClaimRecord fields
    (CPT codes, diagnoses, specialty, requires_auth) so downstream
    agents have useful data even without a real EMR connection.

    Real adapters replace the mock with FHIR R4 queries:
      GET /Encounter?patient={id}&date={dos}
      GET /DocumentReference?patient={id}&category=clinical-note
      GET /Procedure?patient={id}&date={dos}

    Args:
        provider_id:      Provider NPI or internal ID.
        patient_id:       Patient identifier.
        date_of_service:  Optional filter for date of service.
        claim:            The current ClaimRecord — gives adapters
                          full context to generate or fetch records.

    Returns:
        EhrData model with all available clinical documents.

    Raises:
        ToolExecutionError: If retrieval fails.
    """
    from rcm_denial.config.settings import settings
    from rcm_denial.services.data_source_adapters import get_emr_adapter

    logger.info(
        "Retrieving EHR records",
        provider_id=provider_id,
        patient_id=patient_id,
    )

    try:
        adapter = get_emr_adapter(settings.emr_adapter)

        if claim is None:
            from rcm_denial.models.claim import ClaimRecord as CR
            from datetime import date as dt
            claim = CR(
                claim_id="unknown",
                patient_id=patient_id,
                provider_id=provider_id,
                payer_id="unknown",
                date_of_service=date_of_service or dt.today(),
                denial_date=dt.today(),
                carc_code="0",
                billed_amount=1.0,
            )

        ehr = adapter.get_clinical_records(patient_id, provider_id, claim)

        logger.info(
            "EHR records retrieved",
            patient_id=patient_id,
            has_auth=ehr.has_auth_documentation,
            has_notes=ehr.has_encounter_notes,
            encounter_count=len(ehr.encounter_notes),
            source=settings.emr_adapter,
        )
        return ehr

    except Exception as exc:
        raise ToolExecutionError(
            f"Failed to retrieve EHR for patient {patient_id}: {exc}"
        ) from exc
