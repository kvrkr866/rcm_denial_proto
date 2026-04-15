##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: patient_data_tool.py
# Purpose: Retrieves patient demographics and insurance coverage
#          via the configured EMR adapter hook.
#
#          Active adapter is set by settings.emr_adapter:
#            "mock"    — uses ClaimRecord CSV fields + generic placeholders
#            "epic"    — Epic FHIR R4 (implement EpicEMRAdapter)
#            "cerner"  — Cerner Millennium API (implement CernerEMRAdapter)
#            "athena"  — Athena Health API (implement AthenaEMRAdapter)
#
##########################################################

from __future__ import annotations

from rcm_denial.models.claim import ClaimRecord, PatientData
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    """Raised when a tool fails to execute."""


def get_patient_data(patient_id: str, claim: ClaimRecord | None = None) -> PatientData:
    """
    Retrieves patient demographics and insurance coverage.

    Delegates to the EMR adapter configured in settings.emr_adapter.
    The mock adapter uses ClaimRecord fields from the CSV when present,
    avoiding an unnecessary external call. Real adapters will query the
    live EMR/FHIR endpoint.

    Args:
        patient_id: The patient's unique identifier.
        claim:      The current ClaimRecord (used by adapters to check
                    what data is already available from the CSV).

    Returns:
        PatientData model with demographics and coverage.

    Raises:
        ToolExecutionError: If retrieval fails.
    """
    from rcm_denial.config.settings import settings
    from rcm_denial.services.data_source_adapters import get_emr_adapter

    logger.info("Retrieving patient data", patient_id=patient_id)

    try:
        adapter = get_emr_adapter(settings.emr_adapter)

        # Build a minimal stub ClaimRecord if none provided so the
        # adapter always has something to inspect
        if claim is None:
            from rcm_denial.models.claim import ClaimRecord as CR
            from datetime import date
            claim = CR(
                claim_id="unknown",
                patient_id=patient_id,
                provider_id="unknown",
                payer_id="unknown",
                date_of_service=date.today(),
                denial_date=date.today(),
                carc_code="0",
                billed_amount=1.0,
            )

        patient = adapter.get_patient_demographics(patient_id, claim)

        logger.info(
            "Patient data retrieved",
            patient_id=patient_id,
            eligible=patient.is_eligible,
            source=settings.emr_adapter,
        )
        return patient

    except Exception as exc:
        raise ToolExecutionError(f"Failed to retrieve patient {patient_id}: {exc}") from exc
