##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: payer_policy_tool.py
# Purpose: Tool for retrieving payer coverage rules, billing
#          guidelines, and contract terms. Currently uses mock
#          data; replace get_policy_from_api() to integrate
#          with real payer portals or contract management system.
#
##########################################################

from __future__ import annotations

from rcm_denial.models.claim import PayerPolicy
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


_MOCK_PAYER_POLICIES: dict[str, dict] = {
    "BCBS": {
        "payer_name": "Blue Cross Blue Shield",
        "billing_guidelines": [
            "Claims must be submitted within 12 months of date of service",
            "Prior authorization required for all inpatient admissions",
            "Use most specific ICD-10 code available",
            "Modifiers must be appended per CMS guidelines",
        ],
        "covered_cpt_codes": ["99213", "99214", "99215", "27447", "43239", "70553"],
        "prior_auth_required_codes": ["27447", "70553", "43239"],
        "timely_filing_limit_days": 365,
        "appeal_deadline_days": 180,
        "appeal_address": "BCBS Appeals Dept, PO Box 1234, Chicago IL 60601",
        "appeal_fax": "1-800-555-0101",
        "appeal_portal_url": "https://provider.bcbs.com/appeals",
        "contract_rate": 0.80,
        "notes": ["Coordination of Benefits rules apply for dual-coverage members"],
    },
    "AETNA": {
        "payer_name": "Aetna",
        "billing_guidelines": [
            "Claims must be submitted within 90 days for out-of-network providers",
            "Prior authorization required for surgery and advanced imaging",
            "Medical records required for E&M services billed at 99214 or above",
        ],
        "covered_cpt_codes": ["99213", "99214", "27447", "43239", "70553", "93000"],
        "prior_auth_required_codes": ["27447", "70553"],
        "timely_filing_limit_days": 180,
        "appeal_deadline_days": 180,
        "appeal_address": "Aetna Provider Appeals, PO Box 981107, El Paso TX 79998",
        "appeal_fax": "1-800-555-0202",
        "appeal_portal_url": "https://www.aetna.com/provider-appeals",
        "contract_rate": 0.75,
        "notes": [],
    },
    "MEDICARE": {
        "payer_name": "Centers for Medicare & Medicaid Services",
        "billing_guidelines": [
            "Claims must be filed within 1 calendar year of service date",
            "Medicare is always primary payer unless secondary payer rules apply",
            "ABN required if service may not be covered",
            "Correct place-of-service code is mandatory",
        ],
        "covered_cpt_codes": ["99213", "99214", "99215", "27447", "43239", "70553", "93000"],
        "prior_auth_required_codes": [],
        "timely_filing_limit_days": 365,
        "appeal_deadline_days": 120,
        "appeal_address": "Medicare Redetermination Request, PO Box 6703, Indianapolis IN 46206",
        "appeal_fax": "1-855-555-0303",
        "appeal_portal_url": "https://www.cms.gov/medicare/appeals",
        "contract_rate": 1.0,
        "notes": [
            "Medicare Remittance Advice (ERA) issued within 14 days of processing",
            "Redetermination is Level 1 appeal — file within 120 days of denial",
        ],
    },
    "CIGNA": {
        "payer_name": "Cigna",
        "billing_guidelines": [
            "Claims must be submitted within 180 days of date of service",
            "Prior authorization required for DME and advanced imaging",
        ],
        "covered_cpt_codes": ["99213", "99214", "27447", "93000"],
        "prior_auth_required_codes": ["27447"],
        "timely_filing_limit_days": 180,
        "appeal_deadline_days": 180,
        "appeal_address": "Cigna Appeals, PO Box 188004, Chattanooga TN 37422",
        "appeal_fax": "1-800-555-0404",
        "appeal_portal_url": "https://cignaforhcp.cigna.com",
        "contract_rate": 0.78,
        "notes": [],
    },
}


def get_payer_policy(payer_id: str, cpt_codes: list[str] | None = None) -> PayerPolicy:
    """
    Retrieves payer coverage policy, billing rules, and contract terms.

    Real integration: replace with call to contract management system
    or payer API (e.g. Availity, Change Healthcare, NaviNet).

    Args:
        payer_id: Payer identifier (e.g. "BCBS", "AETNA").
        cpt_codes: Optional list of CPT codes to check coverage for.

    Returns:
        PayerPolicy model.

    Raises:
        ToolExecutionError: If policy retrieval fails.
    """
    logger.info("Retrieving payer policy", payer_id=payer_id)

    try:
        raw = _MOCK_PAYER_POLICIES.get(payer_id.upper())

        if raw is None:
            logger.warning("Payer not found in mock store — using generic policy", payer_id=payer_id)
            return PayerPolicy(
                payer_id=payer_id,
                payer_name=f"Unknown Payer ({payer_id})",
                timely_filing_limit_days=365,
                appeal_deadline_days=180,
                notes=["Payer policy not found — manual verification required"],
            )

        policy = PayerPolicy(
            payer_id=payer_id,
            payer_name=raw["payer_name"],
            billing_guidelines=raw.get("billing_guidelines", []),
            covered_cpt_codes=raw.get("covered_cpt_codes", []),
            prior_auth_required_codes=raw.get("prior_auth_required_codes", []),
            timely_filing_limit_days=raw.get("timely_filing_limit_days", 365),
            appeal_deadline_days=raw.get("appeal_deadline_days", 180),
            appeal_address=raw.get("appeal_address"),
            appeal_fax=raw.get("appeal_fax"),
            appeal_portal_url=raw.get("appeal_portal_url"),
            contract_rate=raw.get("contract_rate"),
            notes=raw.get("notes", []),
        )

        logger.info(
            "Payer policy retrieved",
            payer_id=payer_id,
            payer_name=policy.payer_name,
            timely_filing_days=policy.timely_filing_limit_days,
        )
        return policy

    except Exception as exc:
        raise ToolExecutionError(f"Failed to retrieve payer policy for {payer_id}: {exc}") from exc
