##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: test_criteria_checks.py
# Purpose: Unit tests for evals/criteria_checks.py —
#          deterministic structural assertions on LLM output.
#          Tests cover: DenialAnalysis, AppealLetter,
#          EvidenceCheckResult, CorrectionPlan checks,
#          and the golden dataset runner.
#
##########################################################

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


GOLDEN_CASES_PATH = Path(__file__).parent.parent / "data" / "evals" / "golden_cases.json"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _valid_analysis(**overrides) -> dict:
    base = {
        "recommended_action":  "appeal",
        "denial_category":     "medical_necessity",
        "confidence_score":    0.88,
        "root_cause":          "Payer denied MRI as not medically necessary",
        "carc_interpretation": "CARC 50: Non-covered service per payer criteria",
        "reasoning":           "Clinical evidence was insufficient per payer LCD; appeal required with peer-to-peer",
        "correction_possible": False,
    }
    base.update(overrides)
    return base


def _valid_appeal(**overrides) -> dict:
    base = {
        "subject_line":          "Appeal for Denial — Claim #CLM-001",
        "opening_paragraph":     "We formally appeal the denial of the above claim.",
        "denial_summary":        "The claim was denied under CARC 50 citing lack of medical necessity.",
        "clinical_justification": "The service was medically necessary per documented diagnosis and failed conservative therapy.",
        "regulatory_basis":      "Per CMS LCD and Aetna Clinical Policy Bulletin, this service is covered when criteria are met.",
        "closing_paragraph":     "We respectfully request reconsideration.",
        "signature_block":       "Sincerely, Dr. Smith",
    }
    base.update(overrides)
    return base


def _valid_evidence(**overrides) -> dict:
    base = {
        "evidence_sufficient":            True,
        "evidence_gaps":                  [],
        "key_arguments":                  ["Documented failed conservative therapy", "MRI clinically indicated"],
        "recommended_action_confirmed":   "appeal",
        "confidence_score":               0.87,
        "needs_additional_ehr_fetch":     False,
    }
    base.update(overrides)
    return base


def _valid_plan(**overrides) -> dict:
    base = {
        "plan_type":                 "resubmission",
        "code_corrections":          [
            {"original_code": "93320", "corrected_code": "93306",
             "code_type": "CPT", "reason": "Bundling edit"},
        ],
        "documentation_required":    [],
        "resubmission_instructions": ["Remove add-on code, resubmit 93306 only"],
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────
# check_denial_analysis
# ──────────────────────────────────────────────────────────────────────

class TestCheckDenialAnalysis:
    def test_valid_analysis_passes_all(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis())
        assert suite.passed
        assert suite.score == pytest.approx(1.0)
        assert suite.failed_checks == []

    def test_invalid_action_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(recommended_action="fly_to_moon"))
        assert not suite.passed
        assert "action_is_valid_enum" in suite.failed_checks

    def test_invalid_category_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(denial_category="unicorn_denial"))
        assert not suite.passed
        assert "category_is_valid_enum" in suite.failed_checks

    def test_confidence_out_of_range_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(confidence_score=1.5))
        assert "confidence_in_range" in suite.failed_checks

    def test_empty_root_cause_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(root_cause=""))
        assert "root_cause_non_empty" in suite.failed_checks

    def test_writeoff_for_timely_filing_fails_consistency(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(
            recommended_action="write_off",
            denial_category="timely_filing",
            correction_possible=False,
        ))
        assert "action_category_consistency" in suite.failed_checks

    def test_correction_true_with_writeoff_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(
            recommended_action="write_off",
            correction_possible=True,
        ))
        assert "correction_flag_consistency" in suite.failed_checks

    def test_correction_false_with_resubmit_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(
            recommended_action="resubmit",
            correction_possible=False,
        ))
        assert "correction_flag_consistency" in suite.failed_checks

    def test_short_reasoning_fails(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        suite = check_denial_analysis(_valid_analysis(reasoning="Too short"))
        assert "reasoning_is_substantive" in suite.failed_checks

    def test_all_valid_actions_pass(self):
        from rcm_denial.evals.criteria_checks import check_denial_analysis
        for action in ("resubmit", "appeal", "both", "write_off"):
            correction_possible = action != "write_off"
            suite = check_denial_analysis(_valid_analysis(
                recommended_action=action,
                correction_possible=correction_possible,
                denial_category="coding_error",
            ))
            # Should not fail the enum check
            assert "action_is_valid_enum" not in suite.failed_checks


# ──────────────────────────────────────────────────────────────────────
# check_appeal_letter
# ──────────────────────────────────────────────────────────────────────

class TestCheckAppealLetter:
    def test_valid_letter_dict_passes(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        suite = check_appeal_letter(_valid_appeal())
        assert suite.passed
        assert suite.score == pytest.approx(1.0)

    def test_valid_letter_string_passes(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        text = (
            "RE: Appeal for Claim #CLM-001\n\n"
            "We formally appeal the denial. The service was medically necessary "
            "per documented clinical diagnosis. Per CMS guidelines, coverage "
            "is required when criteria are met.\n\n"
            "Respectfully,\nDr. Smith"
        )
        suite = check_appeal_letter(text)
        assert suite.passed

    def test_missing_clinical_justification_fails(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        letter = _valid_appeal(clinical_justification="")
        # Override the full text to remove clinical keywords too
        letter["opening_paragraph"] = "We formally appeal."
        letter["denial_summary"] = "The claim was denied."
        letter["regulatory_basis"] = "Per CMS policy, coverage is required."
        suite = check_appeal_letter(letter)
        assert "has_clinical_justification" in suite.failed_checks

    def test_missing_regulatory_basis_fails(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        letter = _valid_appeal(
            regulatory_basis="",
            closing_paragraph="We request reconsideration. Sincerely, Dr. Smith",
        )
        # strip text to avoid keyword hits
        letter["opening_paragraph"] = "We appeal the denial."
        letter["clinical_justification"] = "The service was medically necessary clinically documented."
        suite = check_appeal_letter(letter)
        assert "has_regulatory_basis" in suite.failed_checks

    def test_no_professional_closing_fails(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        text = (
            "RE: Appeal\n\n"
            "We appeal. The service was medically necessary per clinical diagnosis. "
            "Per CMS guidelines, coverage is required. "
            "Thank you."   # no 'sincerely' / 'respectfully' / 'regards'
        )
        suite = check_appeal_letter(text)
        assert "has_professional_closing" in suite.failed_checks

    def test_too_short_letter_fails(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        suite = check_appeal_letter("Very short letter")
        assert "minimum_length" in suite.failed_checks

    def test_unfilled_placeholder_fails(self):
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        text = (
            "RE: Appeal\n\n"
            "Dear [INSERT PROVIDER NAME]: The service was medically necessary "
            "per documented clinical diagnosis. Per CMS guidelines, coverage "
            "is required when criteria are met.\n\nRespectfully,\nDr. Smith"
        )
        suite = check_appeal_letter(text)
        assert "no_unfilled_placeholders" in suite.failed_checks


# ──────────────────────────────────────────────────────────────────────
# check_evidence_result
# ──────────────────────────────────────────────────────────────────────

class TestCheckEvidenceResult:
    def test_valid_evidence_passes(self):
        from rcm_denial.evals.criteria_checks import check_evidence_result
        suite = check_evidence_result(_valid_evidence())
        assert suite.passed
        assert suite.score == pytest.approx(1.0)

    def test_non_bool_evidence_sufficient_fails(self):
        from rcm_denial.evals.criteria_checks import check_evidence_result
        suite = check_evidence_result(_valid_evidence(evidence_sufficient="yes"))
        assert "evidence_sufficient_is_bool" in suite.failed_checks

    def test_empty_key_arguments_fails(self):
        from rcm_denial.evals.criteria_checks import check_evidence_result
        suite = check_evidence_result(_valid_evidence(key_arguments=[]))
        assert "has_at_least_one_key_argument" in suite.failed_checks

    def test_invalid_action_confirmed_fails(self):
        from rcm_denial.evals.criteria_checks import check_evidence_result
        suite = check_evidence_result(_valid_evidence(recommended_action_confirmed="invalid_action"))
        assert "action_confirmed_is_valid" in suite.failed_checks

    def test_confidence_out_of_range_fails(self):
        from rcm_denial.evals.criteria_checks import check_evidence_result
        suite = check_evidence_result(_valid_evidence(confidence_score=2.0))
        assert "evidence_confidence_in_range" in suite.failed_checks

    def test_gaps_without_fetch_flag_is_advisory_only(self):
        from rcm_denial.evals.criteria_checks import check_evidence_result
        # Should pass (advisory only), but issue a note
        suite = check_evidence_result(_valid_evidence(
            evidence_gaps=["Missing prior auth letter"],
            needs_additional_ehr_fetch=False,
        ))
        gap_result = next(r for r in suite.results if r.check_name == "gaps_with_fetch_flag")
        assert gap_result.passed is True   # advisory, not a hard failure


# ──────────────────────────────────────────────────────────────────────
# check_correction_plan
# ──────────────────────────────────────────────────────────────────────

class TestCheckCorrectionPlan:
    def test_valid_plan_passes(self):
        from rcm_denial.evals.criteria_checks import check_correction_plan
        suite = check_correction_plan(_valid_plan())
        assert suite.passed

    def test_invalid_plan_type_fails(self):
        from rcm_denial.evals.criteria_checks import check_correction_plan
        suite = check_correction_plan(_valid_plan(plan_type="invalidtype"))
        assert "plan_type_is_valid" in suite.failed_checks

    def test_invalid_code_type_fails(self):
        from rcm_denial.evals.criteria_checks import check_correction_plan
        plan = _valid_plan(code_corrections=[
            {"original_code": "99213", "corrected_code": "99214",
             "code_type": "INVALID_TYPE", "reason": "Upcoding"},
        ])
        suite = check_correction_plan(plan)
        assert "code_correction_types_valid" in suite.failed_checks

    def test_empty_corrected_code_fails(self):
        from rcm_denial.evals.criteria_checks import check_correction_plan
        plan = _valid_plan(code_corrections=[
            {"original_code": "99213", "corrected_code": "",
             "code_type": "CPT", "reason": "Correction"},
        ])
        suite = check_correction_plan(plan)
        assert "code_corrections_non_empty" in suite.failed_checks

    def test_resubmission_with_no_content_fails(self):
        from rcm_denial.evals.criteria_checks import check_correction_plan
        plan = {
            "plan_type": "resubmission",
            "code_corrections": [],
            "documentation_required": [],
            "resubmission_instructions": [],
        }
        suite = check_correction_plan(plan)
        assert "resubmission_has_content" in suite.failed_checks

    def test_appeal_plan_no_resubmission_content_ok(self):
        from rcm_denial.evals.criteria_checks import check_correction_plan
        plan = {
            "plan_type": "appeal",
            "code_corrections": [],
            "documentation_required": [],
            "resubmission_instructions": [],
        }
        suite = check_correction_plan(plan)
        # Appeal plans don't require resubmission content
        assert "resubmission_has_content" not in suite.failed_checks


# ──────────────────────────────────────────────────────────────────────
# run_golden_checks
# ──────────────────────────────────────────────────────────────────────

class TestGoldenDataset:
    def test_golden_file_exists(self):
        assert GOLDEN_CASES_PATH.exists(), f"Golden cases file missing: {GOLDEN_CASES_PATH}"

    def test_golden_cases_load_correctly(self):
        with open(GOLDEN_CASES_PATH) as f:
            raw = json.load(f)
        cases = [c for c in raw if "case_id" in c]
        assert len(cases) >= 10, "Expected at least 10 golden cases"

    def test_all_categories_covered(self):
        with open(GOLDEN_CASES_PATH) as f:
            raw = json.load(f)
        cases = [c for c in raw if "case_id" in c]
        categories = {c["expected_category"] for c in cases}
        required = {
            "timely_filing", "medical_necessity", "prior_auth",
            "duplicate_claim", "coding_error", "eligibility",
        }
        assert required.issubset(categories), f"Missing categories: {required - categories}"

    def test_all_actions_covered(self):
        with open(GOLDEN_CASES_PATH) as f:
            raw = json.load(f)
        cases = [c for c in raw if "case_id" in c]
        actions = {c["expected_action"] for c in cases}
        assert actions == {"resubmit", "appeal", "both", "write_off"}

    def test_run_golden_checks_self_consistency(self):
        """Without actual pipeline output, checks run in self-consistency mode."""
        from rcm_denial.evals.criteria_checks import run_golden_checks
        report = run_golden_checks(GOLDEN_CASES_PATH)
        assert report.total_cases >= 10
        assert report.action_accuracy == pytest.approx(1.0)   # self-check always matches
        assert report.category_accuracy == pytest.approx(1.0)
        assert report.avg_composite_score >= 0.9

    def test_run_golden_checks_with_missing_output_dir(self, tmp_path):
        """output_dir present but no files → load failures recorded, still runs."""
        from rcm_denial.evals.criteria_checks import run_golden_checks
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        report = run_golden_checks(GOLDEN_CASES_PATH, output_dir=out_dir)
        # All cases should have load failures since no metadata files exist
        assert len(report.failures) == report.total_cases

    def test_run_golden_checks_missing_file_raises(self, tmp_path):
        from rcm_denial.evals.criteria_checks import run_golden_checks
        with pytest.raises(FileNotFoundError):
            run_golden_checks(tmp_path / "nonexistent_golden.json")

    def test_report_to_dict_is_serializable(self):
        import json as _json
        from rcm_denial.evals.criteria_checks import run_golden_checks
        report = run_golden_checks(GOLDEN_CASES_PATH)
        d = report.to_dict()
        # Should be JSON-serializable without errors
        serialized = _json.dumps(d)
        assert len(serialized) > 100

    def test_sample_appeal_letter_in_golden_passes_check(self):
        """Golden cases with sample_appeal_letter should pass appeal checks."""
        from rcm_denial.evals.criteria_checks import check_appeal_letter
        with open(GOLDEN_CASES_PATH) as f:
            raw = json.load(f)
        cases_with_letter = [c for c in raw if "sample_appeal_letter" in c]
        assert len(cases_with_letter) >= 1, "Expected at least one golden case with sample_appeal_letter"
        for case in cases_with_letter:
            suite = check_appeal_letter(case["sample_appeal_letter"])
            assert suite.passed, (
                f"Case {case['case_id']} appeal letter failed: {suite.failed_checks}"
            )
