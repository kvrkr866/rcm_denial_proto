##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/evals.py
# Purpose: Evaluations page — golden dataset regression,
#          criteria checks, quality signals.
#
##########################################################

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from rcm_denial.web.app import create_header, create_footer


GOLDEN_CASES_PATH = Path("data/evals/golden_cases.json")


@ui.page("/evals")
def evals_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        ui.label("Evaluations").classes("text-3xl font-bold text-gray-800")

        with ui.tabs().classes("w-full") as tabs:
            golden_tab = ui.tab("Golden Dataset")
            quality_tab = ui.tab("Quality Signals")
            check_tab = ui.tab("Check Single Claim")

        with ui.tab_panels(tabs, value=golden_tab).classes("w-full"):
            with ui.tab_panel(golden_tab):
                _golden_dataset_panel()
            with ui.tab_panel(quality_tab):
                _quality_signals_panel()
            with ui.tab_panel(check_tab):
                _check_single_panel()

    create_footer()


# ──────────────────────────────────────────────────────────────────────
# Golden Dataset Regression
# ──────────────────────────────────────────────────────────────────────

def _golden_dataset_panel():
    ui.label("Run structural criteria checks against the golden dataset.") \
        .classes("text-gray-500")

    with ui.row().classes("gap-4 items-end"):
        output_dir_input = ui.input(
            "Pipeline output dir (optional)",
            placeholder="Leave empty for self-consistency check",
        ).classes("w-80")

    results_area = ui.column().classes("w-full gap-4")

    async def run_golden():
        results_area.clear()
        with results_area:
            spinner = ui.spinner("dots", size="lg")

        try:
            from rcm_denial.evals.criteria_checks import run_golden_checks
            out_dir = Path(output_dir_input.value) if output_dir_input.value.strip() else None
            report = run_golden_checks(GOLDEN_CASES_PATH, output_dir=out_dir)

            with results_area:
                results_area.clear()

                # Summary
                action_pct = round(report.action_accuracy * 100, 1)
                cat_pct = round(report.category_accuracy * 100, 1)
                score = report.avg_composite_score

                with ui.row().classes("gap-4"):
                    _result_chip("Cases", f"{report.passed_cases}/{report.total_cases}",
                                 "green" if report.passed_cases == report.total_cases else "orange")
                    _result_chip("Action Accuracy", f"{action_pct}%",
                                 "green" if action_pct >= 80 else "orange")
                    _result_chip("Category Accuracy", f"{cat_pct}%",
                                 "green" if cat_pct >= 80 else "orange")
                    _result_chip("Avg Score", f"{score:.3f}",
                                 "green" if score >= 0.75 else "orange")

                # Per-case table
                cols = [
                    {"name": "case_id",  "label": "Case ID",        "field": "case_id"},
                    {"name": "category", "label": "Category",       "field": "category"},
                    {"name": "expected", "label": "Expected",       "field": "expected"},
                    {"name": "predicted","label": "Predicted",      "field": "predicted"},
                    {"name": "action",   "label": "Action Match",   "field": "action"},
                    {"name": "struct",   "label": "Structural",     "field": "struct"},
                    {"name": "score",    "label": "Score",          "field": "score", "sortable": True},
                ]
                rows = []
                for r in report.case_results:
                    struct_ok = r.analysis_suite is None or r.analysis_suite.passed
                    rows.append({
                        "case_id":  r.case_id,
                        "category": r.denial_category,
                        "expected": r.expected_action,
                        "predicted": r.predicted_action or "N/A",
                        "action":   "PASS" if r.action_match else "FAIL",
                        "struct":   "PASS" if struct_ok else "FAIL",
                        "score":    f"{r.composite_score:.3f}",
                    })

                ui.table(columns=cols, rows=rows, row_key="case_id") \
                    .props("dense flat").classes("w-full mt-4")

                # Failures
                if report.failures:
                    ui.label(f"{len(report.failures)} load warning(s):") \
                        .classes("text-orange-600 text-sm mt-2")
                    for msg in report.failures[:5]:
                        ui.label(f"  {msg}").classes("text-xs text-gray-500")

        except FileNotFoundError as exc:
            with results_area:
                results_area.clear()
                ui.label(f"File not found: {exc}").classes("text-red-600")
        except Exception as exc:
            with results_area:
                results_area.clear()
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Run Golden Checks", icon="science", on_click=run_golden) \
        .props("color=primary").classes("mt-2")


# ──────────────────────────────────────────────────────────────────────
# Quality Signals (from review queue)
# ──────────────────────────────────────────────────────────────────────

def _quality_signals_panel():
    ui.label("Continuous eval signals derived from reviewer actions (ground truth).") \
        .classes("text-gray-500")

    content = ui.column().classes("w-full gap-4")

    async def load_signals():
        content.clear()
        try:
            from rcm_denial.services.review_queue import get_review_stats
            stats = get_review_stats()

            total = stats.get("total_claims", 0)
            if total == 0:
                with content:
                    ui.label("No review queue data yet. Process a batch first.") \
                        .classes("text-gray-400 italic")
                return

            fp_rate = stats.get("first_pass_approval_rate_pct")
            ov_rate = stats.get("override_rate_pct")
            calib = stats.get("confidence_calibration", {})
            reroute = stats.get("reroute_by_stage", {})
            mc_ids = stats.get("multi_cycle_claim_ids", [])

            with content:
                # Metric table
                cols = [
                    {"name": "metric",  "label": "Metric",  "field": "metric"},
                    {"name": "value",   "label": "Value",   "field": "value"},
                    {"name": "target",  "label": "Target",  "field": "target"},
                    {"name": "signal",  "label": "Signal",  "field": "signal"},
                ]
                rows = []
                if fp_rate is not None:
                    signal = "Good" if fp_rate >= 70 else ("Investigate" if fp_rate >= 50 else "Action needed")
                    rows.append({"metric": "First-pass approval rate", "value": f"{fp_rate:.1f}%",
                                 "target": "> 70%", "signal": signal})
                if ov_rate is not None:
                    signal = "Good" if ov_rate <= 5 else ("High" if ov_rate <= 15 else "Critical")
                    rows.append({"metric": "Human override rate", "value": f"{ov_rate:.1f}%",
                                 "target": "< 5%", "signal": signal})
                rows.append({"metric": "Total claims in queue", "value": str(total),
                             "target": "-", "signal": "-"})
                rows.append({"metric": "Multi-cycle claims", "value": str(len(mc_ids)),
                             "target": "Minimize", "signal": "Eval dataset"})

                gap = calib.get("gap")
                if gap is not None:
                    rows.append({"metric": "Confidence calibration gap", "value": f"{gap:+.3f}",
                                 "target": "> 0", "signal": "Good" if gap > 0 else "Miscalibrated"})

                ui.table(columns=cols, rows=rows, row_key="metric") \
                    .props("dense flat").classes("w-full")

                # Multi-cycle claim IDs
                if mc_ids:
                    with ui.expansion(f"Multi-cycle claims ({len(mc_ids)})", icon="warning") \
                            .classes("w-full"):
                        ui.label("Claims that needed >1 review cycle (ideal for manual eval):") \
                            .classes("text-xs text-gray-500")
                        for cid in mc_ids[:20]:
                            ui.label(f"  {cid}").classes("text-xs font-mono")

                # Reroute breakdown
                if reroute:
                    ui.label("Re-route stage distribution:").classes("text-sm font-semibold mt-4")
                    for stage, cnt in sorted(reroute.items(), key=lambda x: -x[1]):
                        with ui.row().classes("gap-2 items-center"):
                            ui.label(stage).classes("w-52 text-sm")
                            ui.linear_progress(value=cnt / max(total, 1)) \
                                .classes("w-48").props("color=blue")
                            ui.label(str(cnt)).classes("text-sm")

        except Exception as exc:
            with content:
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Load Quality Signals", icon="refresh", on_click=load_signals) \
        .props("color=primary").classes("mt-2")

    ui.timer(0.1, load_signals, once=True)


# ──────────────────────────────────────────────────────────────────────
# Check Single Claim
# ──────────────────────────────────────────────────────────────────────

def _check_single_panel():
    ui.label("Run structural criteria checks on a single claim's pipeline output.") \
        .classes("text-gray-500")

    with ui.row().classes("gap-4 items-end"):
        claim_id_input = ui.input("Claim ID", placeholder="e.g. CLM-001").classes("w-48")
        output_dir_input = ui.input("Output directory", value="./output").classes("w-64")

    results_area = ui.column().classes("w-full gap-4")

    async def run_check():
        claim_id = claim_id_input.value.strip()
        if not claim_id:
            ui.notify("Enter a claim ID", type="warning")
            return

        results_area.clear()
        try:
            import json
            meta_path = Path(output_dir_input.value) / claim_id / "submission_metadata.json"
            if not meta_path.exists():
                with results_area:
                    ui.label(f"No output found at {meta_path}").classes("text-red-600")
                return

            with open(meta_path) as f:
                meta = json.load(f)

            from rcm_denial.evals.criteria_checks import (
                check_denial_analysis, check_appeal_letter, check_evidence_result,
            )

            with results_area:
                # Analysis
                suite = check_denial_analysis(meta)
                _show_check_suite(suite)

                # Appeal letter
                if "appeal_letter" in meta:
                    appeal_suite = check_appeal_letter(meta["appeal_letter"])
                    _show_check_suite(appeal_suite)

                # Evidence
                if "evidence_check" in meta:
                    ev_suite = check_evidence_result(meta["evidence_check"])
                    _show_check_suite(ev_suite)

        except Exception as exc:
            with results_area:
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Run Checks", icon="check_circle", on_click=run_check) \
        .props("color=primary").classes("mt-2")


def _show_check_suite(suite) -> None:
    """Display a CheckSuite result as a card."""
    color = "green" if suite.passed else "red"
    with ui.card().classes(f"w-full border-l-4 border-{color}-500 mt-2"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle" if suite.passed else "cancel") \
                .classes(f"text-{color}-600 text-xl")
            ui.label(suite.subject).classes("font-semibold")
            ui.label(f"score: {suite.score:.3f}").classes("text-sm text-gray-500")

        for r in suite.results:
            icon = "check" if r.passed else "close"
            ic = "green" if r.passed else "red"
            with ui.row().classes("items-center gap-2 ml-4"):
                ui.icon(icon).classes(f"text-{ic}-500 text-sm")
                ui.label(r.check_name).classes("text-sm")
            for issue in r.issues:
                ui.label(f"    {issue}").classes("text-xs text-orange-500 ml-8")


def _result_chip(label: str, value: str, color: str) -> None:
    with ui.card().classes(f"px-4 py-2 bg-{color}-50"):
        ui.label(value).classes(f"text-xl font-bold text-{color}-700")
        ui.label(label).classes("text-xs text-gray-500")
