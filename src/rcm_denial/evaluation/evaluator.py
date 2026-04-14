##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: evaluator.py
# Purpose: Evaluation framework measuring 5 quality metrics:
#          denial classification accuracy, CARC interpretation
#          correctness, document completeness, appeal letter
#          quality (LLM-as-judge), and end-to-end latency.
#          Reads test_cases.json and outputs evaluation_report.json
#
##########################################################

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Test case loader
# ------------------------------------------------------------------ #

def load_test_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


# ------------------------------------------------------------------ #
# Metric 1: Denial classification accuracy
# ------------------------------------------------------------------ #

def evaluate_classification_accuracy(
    predicted_action: str,
    expected_action: str,
    predicted_category: str,
    expected_category: str,
) -> dict:
    action_correct = predicted_action.lower() == expected_action.lower()
    category_correct = predicted_category.lower() == expected_category.lower()
    return {
        "action_correct": action_correct,
        "category_correct": category_correct,
        "score": (int(action_correct) + int(category_correct)) / 2.0,
    }


# ------------------------------------------------------------------ #
# Metric 2: CARC interpretation correctness
# ------------------------------------------------------------------ #

def evaluate_carc_interpretation(
    carc_code: str,
    interpretation: str,
    reference_path: Path,
) -> dict:
    try:
        with open(reference_path) as f:
            ref = json.load(f)
        expected_desc = ref.get("carc_codes", {}).get(carc_code, {}).get("description", "")
        if not expected_desc:
            return {"score": 0.5, "note": f"CARC {carc_code} not in reference — cannot evaluate"}

        # Keyword overlap check (simple heuristic; replace with embedding similarity for production)
        expected_keywords = set(expected_desc.lower().split())
        predicted_keywords = set(interpretation.lower().split())
        # Remove common stop words
        stop_words = {"the", "a", "an", "is", "for", "of", "in", "and", "or", "to", "be"}
        expected_keywords -= stop_words
        predicted_keywords -= stop_words

        if not expected_keywords:
            return {"score": 0.5, "note": "Empty reference — cannot evaluate"}

        overlap = len(expected_keywords & predicted_keywords)
        score = min(1.0, overlap / len(expected_keywords))
        return {
            "score": round(score, 3),
            "keyword_overlap": overlap,
            "reference_keywords": len(expected_keywords),
            "note": "keyword overlap heuristic",
        }
    except Exception as exc:
        return {"score": 0.0, "note": f"Evaluation error: {exc}"}


# ------------------------------------------------------------------ #
# Metric 3: Document completeness
# ------------------------------------------------------------------ #

def evaluate_document_completeness(output_dir: Path) -> dict:
    if not output_dir.exists():
        return {"score": 0.0, "found_files": [], "note": "Output directory not found"}

    expected_files = [
        "submission_metadata.json",
        "audit_log.json",
    ]
    expected_patterns = ["*.pdf"]

    found = [f.name for f in output_dir.iterdir() if f.is_file()]
    found_required = [f for f in expected_files if f in found]
    found_pdfs = [f for f in found if f.endswith(".pdf")]

    total_expected = len(expected_files) + 1  # +1 for at least one PDF
    total_found = len(found_required) + (1 if found_pdfs else 0)
    score = total_found / total_expected if total_expected > 0 else 0.0

    return {
        "score": round(score, 3),
        "found_files": found,
        "required_files_present": found_required,
        "pdf_count": len(found_pdfs),
        "note": f"{total_found}/{total_expected} required artifacts present",
    }


# ------------------------------------------------------------------ #
# Metric 4: Appeal letter quality (LLM-as-judge)
# ------------------------------------------------------------------ #

def evaluate_appeal_letter_quality(appeal_letter_text: str) -> dict:
    """
    Uses LLM as judge to score the appeal letter on a 0-5 scale.
    Falls back to heuristic scoring if LLM is unavailable.
    """
    try:
        from langchain_openai import ChatOpenAI
        from rcm_denial.config.settings import settings

        if not settings.openai_api_key or not appeal_letter_text:
            return _heuristic_appeal_quality(appeal_letter_text)

        llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.0,
            max_tokens=512,
            openai_api_key=settings.openai_api_key,
        )

        judge_prompt = f"""You are evaluating an insurance appeal letter for a denied medical claim.
Score this letter from 0 to 5 on these criteria:
- Clarity (is the denial reason clearly stated and addressed?)
- Clinical justification (is medical necessity argued with specifics?)
- Professional tone (appropriate formal language?)
- Completeness (does it include all standard appeal elements?)
- Regulatory basis (does it cite relevant guidelines or regulations?)

Return ONLY a JSON object: {{"score": <0-5 float>, "reasoning": "<brief explanation>"}}

LETTER:
{appeal_letter_text[:2000]}
"""
        response = llm.invoke(judge_prompt)
        result = json.loads(response.content)
        return {
            "score": min(5.0, max(0.0, float(result.get("score", 2.5)))),
            "reasoning": result.get("reasoning", ""),
            "method": "llm_judge",
        }

    except Exception as exc:
        logger.warning("LLM appeal quality judge failed — using heuristic", error=str(exc))
        return _heuristic_appeal_quality(appeal_letter_text)


def _heuristic_appeal_quality(text: str) -> dict:
    """Rule-based fallback for appeal letter quality scoring."""
    if not text:
        return {"score": 0.0, "method": "heuristic", "note": "Empty letter"}
    score = 0.0
    checks = {
        "has_recipient": any(kw in text.lower() for kw in ["to:", "dear", "appeals department"]),
        "has_subject": "re:" in text.lower() or "subject:" in text.lower(),
        "has_clinical_justification": any(kw in text.lower() for kw in ["medically necessary", "clinical", "diagnosis", "treatment"]),
        "has_regulatory_basis": any(kw in text.lower() for kw in ["regulation", "cms", "policy", "guideline", "42 cfr"]),
        "has_closing": any(kw in text.lower() for kw in ["respectfully", "sincerely", "regards"]),
    }
    score = sum(checks.values()) * 1.0  # max 5.0
    return {"score": round(score, 1), "method": "heuristic", "checks": checks}


# ------------------------------------------------------------------ #
# Metric 5: End-to-end latency
# ------------------------------------------------------------------ #

def evaluate_latency(processing_duration_ms: Optional[float]) -> dict:
    if not processing_duration_ms:
        return {"score": 0.5, "note": "Duration not recorded"}
    # Target: < 60s = excellent, < 120s = good, < 300s = acceptable, > 300s = slow
    thresholds = [(60000, 1.0), (120000, 0.8), (300000, 0.5)]
    score = 0.2
    for threshold_ms, s in thresholds:
        if processing_duration_ms <= threshold_ms:
            score = s
            break
    return {
        "score": score,
        "duration_ms": round(processing_duration_ms, 2),
        "duration_seconds": round(processing_duration_ms / 1000, 2),
    }


# ------------------------------------------------------------------ #
# Full evaluation runner
# ------------------------------------------------------------------ #

def run_evaluation(
    test_cases_path: Path,
    output_base_dir: Path,
    reference_path: Path,
) -> dict:
    """
    Runs all 5 evaluation metrics across all test cases.
    Writes evaluation_report.json to output_base_dir.
    """
    test_cases = load_test_cases(test_cases_path)
    results = []

    for tc in test_cases:
        claim_id = tc["claim_id"]
        claim_output_dir = output_base_dir / claim_id
        meta_path = claim_output_dir / "submission_metadata.json"

        # Load actual output metadata
        actual_meta = {}
        if meta_path.exists():
            with open(meta_path) as f:
                actual_meta = json.load(f)

        # Load appeal letter text if available
        appeal_text = ""
        for pdf_path in claim_output_dir.glob("*appeal*.pdf"):
            appeal_text = f"[PDF found: {pdf_path.name}]"  # In prod: extract text from PDF

        # Run metrics
        m1 = evaluate_classification_accuracy(
            predicted_action=actual_meta.get("recommended_action", ""),
            expected_action=tc.get("expected_action", ""),
            predicted_category=actual_meta.get("denial_category", ""),
            expected_category=tc.get("expected_category", ""),
        )
        m2 = evaluate_carc_interpretation(
            carc_code=tc.get("carc_code", ""),
            interpretation=actual_meta.get("root_cause", ""),
            reference_path=reference_path,
        )
        m3 = evaluate_document_completeness(claim_output_dir)
        m4 = evaluate_appeal_letter_quality(appeal_text)
        m5 = evaluate_latency(actual_meta.get("processing_duration_ms"))

        composite = (
            m1["score"] * 0.30 +
            m2["score"] * 0.20 +
            m3["score"] * 0.20 +
            (m4["score"] / 5.0) * 0.20 +
            m5["score"] * 0.10
        )

        results.append({
            "claim_id": claim_id,
            "composite_score": round(composite, 3),
            "classification_accuracy": m1,
            "carc_interpretation": m2,
            "document_completeness": m3,
            "appeal_letter_quality": m4,
            "latency": m5,
        })

    avg_composite = sum(r["composite_score"] for r in results) / len(results) if results else 0
    report = {
        "evaluated_at": datetime.utcnow().isoformat(),
        "total_test_cases": len(results),
        "average_composite_score": round(avg_composite, 3),
        "results": results,
    }

    report_path = output_base_dir / "evaluation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(
        "Evaluation complete",
        cases=len(results),
        avg_score=avg_composite,
        report_path=str(report_path),
    )
    return report


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from rcm_denial.config.settings import settings

    test_cases_path = Path(__file__).parent / "test_cases.json"
    report = run_evaluation(
        test_cases_path=test_cases_path,
        output_base_dir=settings.output_dir,
        reference_path=settings.carc_rarc_reference_path,
    )
    print(f"Evaluation complete. Average composite score: {report['average_composite_score']:.1%}")
