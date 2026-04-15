##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: evals/criteria_checks.py
# Purpose: Deterministic structural assertions on LLM outputs.
#
#          These checks do NOT use an LLM — they are fast, free,
#          and always deterministic.  They answer:
#
#            "Did the LLM produce a structurally valid, internally
#             consistent output for this denial category?"
#
#          Three assertion groups:
#            1. DenialAnalysis checks (analysis_agent output)
#            2. AppealLetter / CorrectionPlan checks (response_agent)
#            3. EvidenceCheckResult checks (evidence_check_agent)
#
#          Each check returns a CriteriaResult with:
#            passed: bool
#            score:  float  (0.0 or 1.0 for binary; partial for multi)
#            issues: list[str]  — human-readable failures
#
#          Golden-dataset runner:
#            run_golden_checks(golden_cases_path) -> GoldenReport
#
##########################################################

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CriteriaResult:
    check_name: str
    passed: bool
    score: float           # 0.0 – 1.0
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "score": round(self.score, 3),
            "issues": self.issues,
        }


@dataclass
class CheckSuite:
    """Aggregated result of running all checks on one output."""
    subject: str          # e.g. "DenialAnalysis" or "AppealLetter"
    results: list[CriteriaResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    @property
    def failed_checks(self) -> list[str]:
        return [r.check_name for r in self.results if not r.passed]

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "passed": self.passed,
            "score": round(self.score, 3),
            "failed_checks": self.failed_checks,
            "results": [r.to_dict() for r in self.results],
        }


# ──────────────────────────────────────────────────────────────────────────────
# 1.  DenialAnalysis checks
# ──────────────────────────────────────────────────────────────────────────────

# Maps denial_category → CARC code prefixes / ranges that should be consistent
_CATEGORY_CARC_MAP: dict[str, list[str]] = {
    "timely_filing":              ["29", "119", "181"],
    "medical_necessity":          ["50", "57", "151", "167", "197"],
    "prior_auth":                 ["15", "197", "278"],
    "duplicate_claim":            ["18", "97"],
    "coding_error":               ["4", "5", "6", "11", "16", "22", "96"],
    "eligibility":                ["27", "31", "181"],
    "coordination_of_benefits":   ["22", "23", "24"],
}

_VALID_ACTIONS = {"resubmit", "appeal", "both", "write_off"}
_VALID_CATEGORIES = {
    "timely_filing", "medical_necessity", "prior_auth", "duplicate_claim",
    "coding_error", "eligibility", "coordination_of_benefits", "other",
}


def check_denial_analysis(analysis: dict) -> CheckSuite:
    """
    Run all structural checks on a DenialAnalysis dict (from submission_metadata.json
    or directly from the model's .model_dump()).

    Expected keys: recommended_action, denial_category, confidence_score,
                   root_cause, carc_interpretation, reasoning, correction_possible
    """
    suite = CheckSuite(subject="DenialAnalysis")

    # ── 1a. Action is a valid enum value ───────────────────────────────
    action = str(analysis.get("recommended_action", "")).lower()
    suite.results.append(CriteriaResult(
        check_name="action_is_valid_enum",
        passed=action in _VALID_ACTIONS,
        score=1.0 if action in _VALID_ACTIONS else 0.0,
        issues=[] if action in _VALID_ACTIONS else [
            f"recommended_action '{action}' not in {_VALID_ACTIONS}"
        ],
    ))

    # ── 1b. Category is a valid enum value ─────────────────────────────
    category = str(analysis.get("denial_category", "")).lower()
    suite.results.append(CriteriaResult(
        check_name="category_is_valid_enum",
        passed=category in _VALID_CATEGORIES,
        score=1.0 if category in _VALID_CATEGORIES else 0.0,
        issues=[] if category in _VALID_CATEGORIES else [
            f"denial_category '{category}' not in valid set"
        ],
    ))

    # ── 1c. Confidence score is in range ───────────────────────────────
    conf = analysis.get("confidence_score", -1)
    conf_ok = isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0
    suite.results.append(CriteriaResult(
        check_name="confidence_in_range",
        passed=conf_ok,
        score=1.0 if conf_ok else 0.0,
        issues=[] if conf_ok else [f"confidence_score={conf!r} outside [0, 1]"],
    ))

    # ── 1d. Root cause is non-empty ────────────────────────────────────
    root_cause = str(analysis.get("root_cause", "")).strip()
    rc_ok = len(root_cause) >= 10
    suite.results.append(CriteriaResult(
        check_name="root_cause_non_empty",
        passed=rc_ok,
        score=1.0 if rc_ok else 0.0,
        issues=[] if rc_ok else ["root_cause is empty or too short (< 10 chars)"],
    ))

    # ── 1e. Action-category consistency ───────────────────────────────
    # write_off should NEVER be recommended for timely_filing (re-submittable)
    # resubmit should never appear for 'appeal only' cases (medical_necessity)
    issues_e: list[str] = []
    if action == "write_off" and category == "timely_filing":
        issues_e.append(
            "write_off recommended for timely_filing — timely filing denials are typically resubmittable"
        )
    if action == "resubmit" and category == "coordination_of_benefits":
        issues_e.append(
            "resubmit alone may be insufficient for COB denial — 'both' or 'appeal' is usually required"
        )
    suite.results.append(CriteriaResult(
        check_name="action_category_consistency",
        passed=len(issues_e) == 0,
        score=1.0 if not issues_e else 0.0,
        issues=issues_e,
    ))

    # ── 1f. correction_possible matches action ─────────────────────────
    correction_possible = analysis.get("correction_possible")
    if correction_possible is not None:
        # write_off implies correction NOT possible
        if action == "write_off" and correction_possible is True:
            issue_f = "correction_possible=True but recommended_action=write_off — contradiction"
        elif action in ("resubmit", "both") and correction_possible is False:
            issue_f = f"correction_possible=False but recommended_action={action!r} — contradiction"
        else:
            issue_f = None
        suite.results.append(CriteriaResult(
            check_name="correction_flag_consistency",
            passed=issue_f is None,
            score=1.0 if issue_f is None else 0.0,
            issues=[issue_f] if issue_f else [],
        ))

    # ── 1g. Reasoning is substantive ──────────────────────────────────
    reasoning = str(analysis.get("reasoning", "")).strip()
    reasoning_ok = len(reasoning) >= 30
    suite.results.append(CriteriaResult(
        check_name="reasoning_is_substantive",
        passed=reasoning_ok,
        score=1.0 if reasoning_ok else 0.0,
        issues=[] if reasoning_ok else ["reasoning is missing or too short (< 30 chars)"],
    ))

    return suite


# ──────────────────────────────────────────────────────────────────────────────
# 2.  AppealLetter checks
# ──────────────────────────────────────────────────────────────────────────────

_CLINICAL_KEYWORDS = {
    "medically necessary", "medical necessity", "clinical", "diagnosis",
    "treatment", "procedure", "icd", "cpt", "documentation", "clinical evidence",
}
_REGULATORY_KEYWORDS = {
    "regulation", "cms", "policy", "guideline", "42 cfr", "coverage criteria",
    "lcd", "ncd", "aetna", "cigna", "uhc", "medicare",
}
_CLOSING_KEYWORDS = {"respectfully", "sincerely", "regards", "yours truly"}


def check_appeal_letter(letter: dict | str) -> CheckSuite:
    """
    Run structural checks on an AppealLetter.

    Accepts either:
      - a dict (AppealLetter.model_dump())
      - a plain string (the full letter text from .full_text)
    """
    suite = CheckSuite(subject="AppealLetter")

    if isinstance(letter, dict):
        full_text = _assemble_letter_text(letter)
        subject_line = str(letter.get("subject_line", "")).strip()
        clinical_just = str(letter.get("clinical_justification", "")).strip()
        regulatory = str(letter.get("regulatory_basis", "")).strip()
        opening = str(letter.get("opening_paragraph", "")).strip()
        closing = str(letter.get("closing_paragraph", "")).strip()
        sender_name = str(letter.get("sender_name", "")).strip()
    else:
        full_text = str(letter)
        subject_line = ""
        clinical_just = ""
        regulatory = ""
        opening = ""
        closing = ""
        sender_name = ""

    full_lower = full_text.lower()

    # ── 2a. Subject line present ───────────────────────────────────────
    has_subject = bool(subject_line) or "re:" in full_lower or "subject:" in full_lower
    suite.results.append(CriteriaResult(
        check_name="has_subject_line",
        passed=has_subject,
        score=1.0 if has_subject else 0.0,
        issues=[] if has_subject else ["Letter has no subject/RE line"],
    ))

    # ── 2b. Clinical justification present ────────────────────────────
    has_clinical = (
        len(clinical_just) >= 20 or
        any(kw in full_lower for kw in _CLINICAL_KEYWORDS)
    )
    suite.results.append(CriteriaResult(
        check_name="has_clinical_justification",
        passed=has_clinical,
        score=1.0 if has_clinical else 0.0,
        issues=[] if has_clinical else [
            "Letter lacks clinical justification (no clinical keywords found)"
        ],
    ))

    # ── 2c. Regulatory basis cited ────────────────────────────────────
    has_regulatory = (
        len(regulatory) >= 20 or
        any(kw in full_lower for kw in _REGULATORY_KEYWORDS)
    )
    suite.results.append(CriteriaResult(
        check_name="has_regulatory_basis",
        passed=has_regulatory,
        score=1.0 if has_regulatory else 0.0,
        issues=[] if has_regulatory else [
            "Letter does not cite any regulatory basis or payer policy"
        ],
    ))

    # ── 2d. Professional closing ──────────────────────────────────────
    has_closing = any(kw in full_lower for kw in _CLOSING_KEYWORDS)
    suite.results.append(CriteriaResult(
        check_name="has_professional_closing",
        passed=has_closing,
        score=1.0 if has_closing else 0.0,
        issues=[] if has_closing else ["Letter has no professional closing statement"],
    ))

    # ── 2e. Minimum length ─────────────────────────────────────────────
    min_length = 200
    length_ok = len(full_text) >= min_length
    suite.results.append(CriteriaResult(
        check_name="minimum_length",
        passed=length_ok,
        score=1.0 if length_ok else min(1.0, len(full_text) / min_length),
        issues=[] if length_ok else [
            f"Letter is only {len(full_text)} chars (minimum: {min_length})"
        ],
    ))

    # ── 2f. No placeholder text left in ───────────────────────────────
    placeholder_patterns = [
        r"\[INSERT", r"\[PLACEHOLDER", r"\[TBD\]", r"\[YOUR NAME\]",
        r"\[PROVIDER NAME\]", r"\[DATE\]", r"___+",
    ]
    placeholders_found = [
        pat for pat in placeholder_patterns
        if re.search(pat, full_text, re.IGNORECASE)
    ]
    suite.results.append(CriteriaResult(
        check_name="no_unfilled_placeholders",
        passed=len(placeholders_found) == 0,
        score=1.0 if not placeholders_found else 0.0,
        issues=[f"Unfilled placeholder found: {p}" for p in placeholders_found],
    ))

    return suite


def _assemble_letter_text(letter: dict) -> str:
    sections = [
        letter.get("opening_paragraph", ""),
        letter.get("denial_summary", ""),
        letter.get("clinical_justification", ""),
        letter.get("regulatory_basis", ""),
        letter.get("closing_paragraph", ""),
        letter.get("signature_block", ""),
    ]
    return "\n\n".join(s for s in sections if s)


# ──────────────────────────────────────────────────────────────────────────────
# 3.  EvidenceCheckResult checks
# ──────────────────────────────────────────────────────────────────────────────

def check_evidence_result(evidence: dict) -> CheckSuite:
    """
    Run structural checks on an EvidenceCheckResult dict.

    Expected keys: evidence_sufficient, key_arguments, confidence_score,
                   recommended_action_confirmed, evidence_gaps
    """
    suite = CheckSuite(subject="EvidenceCheckResult")

    # ── 3a. evidence_sufficient is a boolean ──────────────────────────
    ev_suf = evidence.get("evidence_sufficient")
    bool_ok = isinstance(ev_suf, bool)
    suite.results.append(CriteriaResult(
        check_name="evidence_sufficient_is_bool",
        passed=bool_ok,
        score=1.0 if bool_ok else 0.0,
        issues=[] if bool_ok else [
            f"evidence_sufficient={ev_suf!r} is not a boolean"
        ],
    ))

    # ── 3b. At least one key argument provided ─────────────────────────
    key_args = evidence.get("key_arguments", [])
    has_args = isinstance(key_args, list) and len(key_args) >= 1
    suite.results.append(CriteriaResult(
        check_name="has_at_least_one_key_argument",
        passed=has_args,
        score=1.0 if has_args else 0.0,
        issues=[] if has_args else ["key_arguments list is empty"],
    ))

    # ── 3c. Action confirmed is valid ─────────────────────────────────
    action_conf = str(evidence.get("recommended_action_confirmed", "")).lower()
    action_ok = action_conf in _VALID_ACTIONS
    suite.results.append(CriteriaResult(
        check_name="action_confirmed_is_valid",
        passed=action_ok,
        score=1.0 if action_ok else 0.0,
        issues=[] if action_ok else [
            f"recommended_action_confirmed='{action_conf}' not in {_VALID_ACTIONS}"
        ],
    ))

    # ── 3d. If evidence gaps exist, needs_additional_ehr_fetch should be True or gaps noted ──
    gaps = evidence.get("evidence_gaps", [])
    needs_fetch = evidence.get("needs_additional_ehr_fetch", False)
    if gaps and not needs_fetch:
        # Not a hard failure — LLM may decide gaps are not EHR-fetchable
        suite.results.append(CriteriaResult(
            check_name="gaps_with_fetch_flag",
            passed=True,   # advisory only
            score=1.0,
            issues=[
                f"evidence_gaps present ({len(gaps)}) but needs_additional_ehr_fetch=False "
                f"— verify this is intentional"
            ],
        ))
    else:
        suite.results.append(CriteriaResult(
            check_name="gaps_with_fetch_flag",
            passed=True,
            score=1.0,
        ))

    # ── 3e. Confidence in range ────────────────────────────────────────
    conf = evidence.get("confidence_score", -1)
    conf_ok = isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0
    suite.results.append(CriteriaResult(
        check_name="evidence_confidence_in_range",
        passed=conf_ok,
        score=1.0 if conf_ok else 0.0,
        issues=[] if conf_ok else [f"confidence_score={conf!r} outside [0, 1]"],
    ))

    return suite


# ──────────────────────────────────────────────────────────────────────────────
# 4.  CorrectionPlan checks
# ──────────────────────────────────────────────────────────────────────────────

_VALID_PLAN_TYPES = {"resubmission", "appeal", "both"}
_VALID_CODE_TYPES = {"CPT", "ICD10", "HCPCS", "modifier"}


def check_correction_plan(plan: dict) -> CheckSuite:
    """
    Run structural checks on a CorrectionPlan dict.

    Expected keys: plan_type, code_corrections, documentation_required,
                   resubmission_instructions
    """
    suite = CheckSuite(subject="CorrectionPlan")

    # ── 4a. Plan type is valid ─────────────────────────────────────────
    plan_type = str(plan.get("plan_type", "")).lower()
    pt_ok = plan_type in _VALID_PLAN_TYPES
    suite.results.append(CriteriaResult(
        check_name="plan_type_is_valid",
        passed=pt_ok,
        score=1.0 if pt_ok else 0.0,
        issues=[] if pt_ok else [
            f"plan_type='{plan_type}' not in {_VALID_PLAN_TYPES}"
        ],
    ))

    # ── 4b. Code corrections have valid code_type ─────────────────────
    corrections = plan.get("code_corrections", [])
    bad_code_types = [
        c.get("code_type") for c in corrections
        if c.get("code_type") not in _VALID_CODE_TYPES
    ]
    suite.results.append(CriteriaResult(
        check_name="code_correction_types_valid",
        passed=len(bad_code_types) == 0,
        score=1.0 if not bad_code_types else 0.0,
        issues=[f"Invalid code_type values: {bad_code_types}"] if bad_code_types else [],
    ))

    # ── 4c. Each code correction has non-empty corrected_code ──────────
    empty_corrections = [
        i for i, c in enumerate(corrections)
        if not str(c.get("corrected_code", "")).strip()
    ]
    suite.results.append(CriteriaResult(
        check_name="code_corrections_non_empty",
        passed=len(empty_corrections) == 0,
        score=1.0 if not empty_corrections else 0.0,
        issues=[f"Empty corrected_code at index(es): {empty_corrections}"]
        if empty_corrections else [],
    ))

    # ── 4d. At least one instruction or correction for resubmission ───
    if plan_type in ("resubmission", "both"):
        has_content = bool(corrections) or bool(
            plan.get("resubmission_instructions")
        ) or bool(plan.get("documentation_required"))
        suite.results.append(CriteriaResult(
            check_name="resubmission_has_content",
            passed=has_content,
            score=1.0 if has_content else 0.0,
            issues=[] if has_content else [
                "Resubmission plan has no code corrections, instructions, or documentation"
            ],
        ))

    return suite


# ──────────────────────────────────────────────────────────────────────────────
# 5.  Golden-dataset runner
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class GoldenCaseResult:
    case_id: str
    denial_category: str
    expected_action: str
    predicted_action: str
    action_match: bool
    category_match: bool
    analysis_suite: Optional[CheckSuite] = None
    appeal_suite: Optional[CheckSuite] = None
    evidence_suite: Optional[CheckSuite] = None
    correction_suite: Optional[CheckSuite] = None

    @property
    def composite_score(self) -> float:
        suites = [s for s in [
            self.analysis_suite, self.appeal_suite,
            self.evidence_suite, self.correction_suite,
        ] if s is not None]
        structural_score = (
            sum(s.score for s in suites) / len(suites) if suites else 0.5
        )
        action_score = (
            (int(self.action_match) + int(self.category_match)) / 2.0
        )
        return round(0.4 * action_score + 0.6 * structural_score, 3)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "denial_category": self.denial_category,
            "expected_action": self.expected_action,
            "predicted_action": self.predicted_action,
            "action_match": self.action_match,
            "category_match": self.category_match,
            "composite_score": self.composite_score,
            "analysis_suite": self.analysis_suite.to_dict() if self.analysis_suite else None,
            "appeal_suite":   self.appeal_suite.to_dict()   if self.appeal_suite else None,
            "evidence_suite": self.evidence_suite.to_dict() if self.evidence_suite else None,
            "correction_suite": self.correction_suite.to_dict() if self.correction_suite else None,
        }


@dataclass
class GoldenReport:
    total_cases: int
    passed_cases: int
    action_accuracy: float        # % where predicted_action == expected_action
    category_accuracy: float      # % where category match
    avg_composite_score: float
    case_results: list[GoldenCaseResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_cases":          self.total_cases,
            "passed_cases":         self.passed_cases,
            "action_accuracy_pct":  round(self.action_accuracy * 100, 1),
            "category_accuracy_pct": round(self.category_accuracy * 100, 1),
            "avg_composite_score":  self.avg_composite_score,
            "failures":             self.failures,
            "case_results":         [r.to_dict() for r in self.case_results],
        }


def run_golden_checks(
    golden_cases_path: Path,
    output_dir: Optional[Path] = None,
) -> GoldenReport:
    """
    Run criteria checks against the golden dataset.

    Each golden case is matched against submission_metadata.json in output_dir/<case_id>/
    if output_dir is provided.  Otherwise only the structural checks that can be run
    against the golden case's embedded expected_* fields are executed.

    Args:
        golden_cases_path: Path to data/evals/golden_cases.json
        output_dir:        Path to the pipeline output directory (optional).
                           When provided, actual LLM output is loaded and checked.

    Returns:
        GoldenReport dataclass (serialisable via .to_dict())
    """
    if not golden_cases_path.exists():
        raise FileNotFoundError(f"Golden cases file not found: {golden_cases_path}")

    with open(golden_cases_path) as f:
        raw: list[dict] = json.load(f)

    # Skip comment/metadata entries (no case_id key)
    cases = [c for c in raw if "case_id" in c]

    case_results: list[GoldenCaseResult] = []
    failures: list[str] = []

    for case in cases:
        case_id = case.get("case_id", "unknown")
        expected_action   = str(case.get("expected_action", "")).lower()
        expected_category = str(case.get("expected_category", "")).lower()

        # Try to load actual pipeline output
        actual_meta: dict = {}
        if output_dir:
            meta_path = output_dir / case_id / "submission_metadata.json"
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        actual_meta = json.load(f)
                except Exception as exc:
                    failures.append(f"{case_id}: failed to load metadata — {exc}")
            else:
                failures.append(f"{case_id}: no pipeline output found at {meta_path}")

        # When no actual output, use expected fields as a self-consistency check
        analysis_input = actual_meta if actual_meta else {
            "recommended_action":  case.get("expected_action", ""),
            "denial_category":     case.get("expected_category", ""),
            "confidence_score":    case.get("expected_confidence", 0.85),
            "root_cause":          case.get("root_cause_hint", "No root cause provided in golden case"),
            "carc_interpretation": case.get("carc_interpretation_hint", ""),
            "reasoning":           case.get("reasoning_hint", "No reasoning provided in golden case"),
            "correction_possible": case.get("correction_possible", True),
        }

        predicted_action   = str(analysis_input.get("recommended_action", "")).lower()
        predicted_category = str(analysis_input.get("denial_category", "")).lower()

        # Run suites
        analysis_suite  = check_denial_analysis(analysis_input)

        appeal_suite = None
        if "appeal_letter" in actual_meta:
            appeal_suite = check_appeal_letter(actual_meta["appeal_letter"])
        elif case.get("expected_action") in ("appeal", "both"):
            # Use golden case sample letter if provided
            sample = case.get("sample_appeal_letter")
            if sample:
                appeal_suite = check_appeal_letter(sample)

        gcr = GoldenCaseResult(
            case_id=case_id,
            denial_category=expected_category,
            expected_action=expected_action,
            predicted_action=predicted_action,
            action_match=(predicted_action == expected_action),
            category_match=(predicted_category == expected_category),
            analysis_suite=analysis_suite,
            appeal_suite=appeal_suite,
        )
        case_results.append(gcr)

    total      = len(case_results)
    n_action   = sum(1 for r in case_results if r.action_match)
    n_category = sum(1 for r in case_results if r.category_match)
    n_passed   = sum(
        1 for r in case_results
        if r.action_match and r.category_match
        and (r.analysis_suite is None or r.analysis_suite.passed)
    )
    avg_score = (
        sum(r.composite_score for r in case_results) / total if total else 0.0
    )

    report = GoldenReport(
        total_cases=total,
        passed_cases=n_passed,
        action_accuracy=n_action / total if total else 0.0,
        category_accuracy=n_category / total if total else 0.0,
        avg_composite_score=round(avg_score, 3),
        case_results=case_results,
        failures=failures,
    )

    logger.info(
        "Golden criteria checks complete",
        total_cases=total,
        passed=n_passed,
        action_accuracy_pct=round(report.action_accuracy * 100, 1),
        avg_score=round(avg_score, 3),
    )

    return report
