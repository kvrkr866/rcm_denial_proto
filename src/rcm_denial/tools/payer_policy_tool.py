##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: payer_policy_tool.py
# Purpose: Retrieves payer coverage rules, billing guidelines,
#          and contract terms via the configured payer adapter hook.
#
#          Active adapter is set by settings.payer_adapter:
#            "mock"              — uses ClaimRecord CSV payer fields + CARC hints
#            "availity"          — Availity REST API
#            "change_healthcare" — Change Healthcare (Optum) API
#            "naviNet"           — NaviNet portal integration
#
##########################################################

from __future__ import annotations

from rcm_denial.models.claim import ClaimRecord, PayerPolicy
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


def get_payer_policy(
    payer_id: str,
    cpt_codes: list[str] | None = None,
    claim: ClaimRecord | None = None,
) -> PayerPolicy:
    """
    Retrieves payer coverage policy, billing rules, and contract terms.

    Delegates to the payer adapter configured in settings.payer_adapter.
    The mock adapter uses ClaimRecord fields from the CSV (payer_name,
    payer_portal_url, payer_filing_deadline_days, requires_auth) so the
    policy reflects real data from the CSV whenever available.

    Real adapters replace the mock with calls to payer portals or
    clearinghouses (Availity, Change Healthcare, NaviNet, direct EDI).

    Args:
        payer_id:   Payer identifier.
        cpt_codes:  CPT codes to check coverage for.
        claim:      The current ClaimRecord — gives adapters full context
                    to build or fetch policy data.

    Returns:
        PayerPolicy model.

    Raises:
        ToolExecutionError: If policy retrieval fails.
    """
    from rcm_denial.config.settings import settings
    from rcm_denial.services.data_source_adapters import get_payer_adapter

    logger.info("Retrieving payer policy", payer_id=payer_id)

    try:
        adapter = get_payer_adapter(settings.payer_adapter)

        if claim is None:
            from rcm_denial.models.claim import ClaimRecord as CR
            from datetime import date
            claim = CR(
                claim_id="unknown",
                patient_id="unknown",
                provider_id="unknown",
                payer_id=payer_id,
                date_of_service=date.today(),
                denial_date=date.today(),
                carc_code="0",
                billed_amount=1.0,
            )

        policy = adapter.get_policy(payer_id, cpt_codes or [], claim)

        logger.info(
            "Payer policy retrieved",
            payer_id=payer_id,
            payer_name=policy.payer_name,
            timely_filing_days=policy.timely_filing_limit_days,
            source=settings.payer_adapter,
        )
        return policy

    except Exception as exc:
        raise ToolExecutionError(
            f"Failed to retrieve payer policy for {payer_id}: {exc}"
        ) from exc


def get_appeal_instructions(payer_id: str, carc_code: str) -> dict:
    """
    Returns payer-specific appeal steps for a given CARC denial code.

    Useful for the appeal prep agent to build targeted appeal letters.
    Delegates to the payer adapter.
    """
    from rcm_denial.config.settings import settings
    from rcm_denial.services.data_source_adapters import get_payer_adapter

    adapter = get_payer_adapter(settings.payer_adapter)
    return adapter.get_appeal_instructions(payer_id, carc_code)
