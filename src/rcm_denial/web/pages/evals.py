##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/evals.py
# Purpose: Evaluations — accuracy checks and quality signals
#          for the demo audience (billing managers).
#
#          Technical evals (tool perf, LLM stats, fallback
#          rates, routing consistency) are in Grafana.
#
##########################################################

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from rcm_denial.web.layout import create_header, create_footer


GOLDEN_CASES_PATH = Path("data/evals/golden_cases.json")


@ui.page("/evals")
def evals_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-3"):
        ui.label("Evaluations").classes("text-2xl font-bold text-gray-800")
        ui.label("Accuracy checks and quality signals for denial processing") \
            .classes("text-sm text-gray-500 -mt-2")

        with ui.tabs().classes("w-full") as tabs:
            golden_tab = ui.tab("Accuracy Check")
            signals_tab = ui.tab("Quality Signals")

        with ui.tab_panels(tabs, value=golden_tab).classes("w-full"):
            with ui.tab_panel(golden_tab):
                _golden_dataset_panel()
            with ui.tab_panel(signals_tab):
                _quality_signals_panel()

    create_footer()


# ──────────────────────────────────────────────────────────────────────
# TAB: Golden Dataset — Accuracy Check
# ──────────────────────────────────────────────────────────────────────

def _golden_dataset_panel():
    ui.label(
        "Run accuracy checks against 14 labeled denial cases covering all 7 denial categories. "
        "This verifies the AI correctly identifies the denial type and recommends the right action."
    ).classes("text-sm text-gray-500")

    with ui.row().classes("gap-4 items-end"):
        output_dir_input = ui.input(
            "Pipeline output dir (optional)",
            placeholder="Leave empty for self-consistency check",
        ).classes("w-80").props("dense outlined")

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

                action_pct = round(report.action_accuracy * 100, 1)
                cat_pct = round(report.category_accuracy * 100, 1)
                score = report.avg_composite_score

                # Summary cards
                with ui.grid(columns=4).classes("w-full gap-3"):
                    _kpi("Cases Passed", f"{report.passed_cases}/{report.total_cases}",
                         "green" if report.passed_cases == report.total_cases else "orange")
                    _kpi("Action Accuracy", f"{action_pct}%",
                         "green" if action_pct >= 80 else "orange")
                    _kpi("Category Accuracy", f"{cat_pct}%",
                         "green" if cat_pct >= 80 else "orange")
                    _kpi("Avg Score", f"{score:.3f}",
                         "green" if score >= 0.75 else "orange")

                # Interpretation
                if report.passed_cases == report.total_cases:
                    ui.label("All golden cases pass — AI correctly identifies denial types and actions.") \
                        .classes("text-sm text-green-600 font-semibold mt-2")
                else:
                    failed = report.total_cases - report.passed_cases
                    ui.label(f"{failed} case(s) did not pass — review the table below for details.") \
                        .classes("text-sm text-orange-600 font-semibold mt-2")

                # Per-case results table
                cols = [
                    {"name": "case_id", "label": "Case ID", "field": "case_id"},
                    {"name": "category", "label": "Denial Category", "field": "category"},
                    {"name": "expected", "label": "Expected Action", "field": "expected"},
                    {"name": "predicted", "label": "AI Action", "field": "predicted"},
                    {"name": "match", "label": "Match", "field": "match"},
                    {"name": "score", "label": "Score", "field": "score", "sortable": True},
                ]
                rows = [{
                    "case_id": r.case_id,
                    "category": r.denial_category.replace("_", " ").title(),
                    "expected": r.expected_action.upper(),
                    "predicted": (r.predicted_action or "N/A").upper(),
                    "match": "PASS" if r.action_match else "FAIL",
                    "score": f"{r.composite_score:.3f}",
                } for r in report.case_results]
                ui.table(columns=cols, rows=rows, row_key="case_id") \
                    .props("dense flat").classes("w-full mt-3")

                # Category coverage summary
                categories_tested = {r.denial_category for r in report.case_results}
                ui.label(
                    f"Categories tested: {', '.join(c.replace('_', ' ').title() for c in sorted(categories_tested))}"
                ).classes("text-xs text-gray-500 mt-2")

        except FileNotFoundError:
            with results_area:
                results_area.clear()
                ui.label("Golden cases file not found at data/evals/golden_cases.json") \
                    .classes("text-red-600")
        except Exception as exc:
            with results_area:
                results_area.clear()
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Run Accuracy Check", icon="science", on_click=run_golden) \
        .props("color=primary dense no-caps").classes("mt-2")


# ──────────────────────────────────────────────────────────────────────
# TAB: Quality Signals — from reviewer actions
# ──────────────────────────────────────────────────────────────────────

def _quality_signals_panel():
    ui.label(
        "Quality signals derived from human reviewer actions. "
        "When reviewers approve, re-route, or override claims, their actions "
        "serve as ground truth for AI quality measurement."
    ).classes("text-sm text-gray-500")

    container = ui.column().classes("w-full gap-4")

    async def load():
        container.clear()
        try:
            from rcm_denial.services.review_queue import get_review_stats
            stats = get_review_stats()

            total = stats.get("total_claims", 0)
            if total == 0:
                with container:
                    ui.label("No review data yet. Process and review some claims first.") \
                        .classes("text-gray-400 italic")
                return

            fp_rate = stats.get("first_pass_approval_rate_pct")
            ov_rate = stats.get("override_rate_pct")
            calib = stats.get("confidence_calibration", {})
            reroute = stats.get("reroute_by_stage", {})
            mc_ids = stats.get("multi_cycle_claim_ids", [])

            with container:
                # Summary cards
                with ui.grid(columns=4).classes("w-full gap-3"):
                    if fp_rate is not None:
                        color = "green" if fp_rate >= 70 else ("orange" if fp_rate >= 50 else "red")
                        _kpi("First-Pass Approval", f"{fp_rate:.1f}%", color)
                    else:
                        _kpi("First-Pass Approval", "--", "gray")

                    if ov_rate is not None:
                        color = "green" if ov_rate <= 5 else ("orange" if ov_rate <= 15 else "red")
                        _kpi("Override Rate", f"{ov_rate:.1f}%", color)
                    else:
                        _kpi("Override Rate", "--", "gray")

                    _kpi("Total Reviewed", str(total), "blue")
                    _kpi("Multi-Cycle Claims", str(len(mc_ids)),
                         "orange" if mc_ids else "green")

                # Meaning
                ui.label("What These Numbers Mean").classes("text-sm font-semibold text-gray-700 mt-3")
                metrics_table = [
                    {"metric": "First-Pass Approval", "target": "> 70%",
                     "meaning": "% of claims the AI got right without human correction"},
                    {"metric": "Override Rate", "target": "< 5%",
                     "meaning": "% of claims where human had to completely rewrite the AI output"},
                    {"metric": "Multi-Cycle Claims", "target": "Minimize",
                     "meaning": "Claims that needed more than one review cycle — hardest cases"},
                ]
                cols = [
                    {"name": "metric", "label": "Metric", "field": "metric"},
                    {"name": "target", "label": "Target", "field": "target"},
                    {"name": "meaning", "label": "What It Measures", "field": "meaning"},
                ]
                ui.table(columns=cols, rows=metrics_table, row_key="metric") \
                    .props("dense flat").classes("w-full")

                # Confidence calibration
                gap = calib.get("gap")
                if gap is not None:
                    ui.label("AI Confidence Calibration").classes("text-sm font-semibold text-gray-700 mt-4")
                    avg_app = calib.get("avg_conf_approved_first_pass")
                    avg_rer = calib.get("avg_conf_rerouted")
                    color = "green" if gap > 0 else "red"

                    with ui.row().classes("gap-6 items-center"):
                        if avg_app is not None:
                            ui.label(f"Approved claims avg confidence: {avg_app:.0%}") \
                                .classes("text-sm text-green-600")
                        if avg_rer is not None:
                            ui.label(f"Re-routed claims avg confidence: {avg_rer:.0%}") \
                                .classes("text-sm text-orange-600")
                        ui.label(f"Gap: {gap:+.1%}").classes(f"text-lg font-bold text-{color}-600")

                    if gap > 0:
                        ui.label(
                            "Well-calibrated: AI is more confident on claims it gets right."
                        ).classes("text-xs text-green-600")
                    else:
                        ui.label(
                            "Miscalibrated: AI is overconfident on claims it gets wrong. Needs investigation."
                        ).classes("text-xs text-red-600")

                # Re-route hotspots
                if reroute:
                    ui.label("Re-route Hotspots").classes("text-sm font-semibold text-gray-700 mt-4")
                    ui.label("Which pipeline stage gets sent back for rework most often:") \
                        .classes("text-xs text-gray-500")
                    for stage, cnt in sorted(reroute.items(), key=lambda x: -x[1]):
                        with ui.row().classes("gap-2 items-center"):
                            ui.label(stage.replace("_", " ").title()).classes("w-48 text-sm")
                            ui.linear_progress(value=cnt / max(total, 1)) \
                                .classes("w-48").props("color=orange")
                            ui.label(f"{cnt} claims").classes("text-sm text-gray-500")

                # Multi-cycle claim IDs
                if mc_ids:
                    with ui.expansion(
                        f"Multi-cycle claims ({len(mc_ids)}) — review these for patterns",
                        icon="warning",
                    ).classes("w-full mt-2"):
                        for cid in mc_ids[:20]:
                            ui.label(f"  {cid}").classes("text-xs font-mono")
                        if len(mc_ids) > 20:
                            ui.label(f"  ... and {len(mc_ids) - 20} more") \
                                .classes("text-xs text-gray-400")

        except Exception as exc:
            with container:
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Load Quality Signals", icon="refresh", on_click=load) \
        .props("color=primary dense no-caps")
    ui.timer(0.1, load, once=True)


def _kpi(label: str, value: str, color: str) -> None:
    with ui.card().classes(f"bg-{color}-50 h-16 flex items-center justify-center px-3"):
        with ui.column().classes("items-center gap-0"):
            ui.label(value).classes(f"text-lg font-bold text-{color}-700")
            ui.label(label).classes("text-[10px] text-gray-500 text-center leading-tight")
