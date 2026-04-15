##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/stats.py
# Purpose: Statistics dashboard — pipeline results, LLM
#          cost, review queue, submissions, write-offs,
#          eval quality signals.  Mirrors `rcm-denial stats`.
#
##########################################################

from __future__ import annotations

from nicegui import ui

from rcm_denial.web.app import create_header, create_footer


@ui.page("/stats")
def stats_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        ui.label("Statistics").classes("text-3xl font-bold text-gray-800")

        with ui.row().classes("gap-4 items-end"):
            batch_input = ui.input("Batch ID", placeholder="All batches").classes("w-48")
            refresh_btn = ui.button("Load Stats", icon="refresh").props("color=primary")

        content = ui.column().classes("w-full gap-6")

        async def load_stats():
            content.clear()
            batch_id = batch_input.value or ""

            with content:
                try:
                    _build_stats_panels(batch_id)
                except Exception as exc:
                    ui.label(f"Error loading stats: {exc}").classes("text-red-600")

        refresh_btn.on_click(load_stats)
        ui.timer(0.1, load_stats, once=True)

    create_footer()


def _build_stats_panels(batch_id: str) -> None:
    """Build all stats panels. Called inside the content container."""

    # ── 1. Pipeline Results ───────────────────────────────────
    ui.label("Pipeline Results").classes("text-xl font-semibold text-gray-700")
    try:
        from rcm_denial.services.metrics_service import get_current_metrics
        m = get_current_metrics(batch_id=batch_id)

        pipeline = m.get("pipeline", {})
        if not pipeline:
            ui.label("No pipeline results found.").classes("text-gray-400 italic")
        else:
            cols = [
                {"name": "status",   "label": "Status",       "field": "status"},
                {"name": "pkg",      "label": "Package Type", "field": "pkg"},
                {"name": "count",    "label": "Count",        "field": "count",     "sortable": True},
                {"name": "avg_dur",  "label": "Avg Duration", "field": "avg_dur"},
                {"name": "llm",      "label": "LLM Calls",   "field": "llm"},
            ]
            rows = []
            for key, data in sorted(pipeline.items()):
                parts = key.split("_", 1)
                rows.append({
                    "status":  parts[0],
                    "pkg":     parts[1] if len(parts) > 1 else "-",
                    "count":   data["count"],
                    "avg_dur": f"{data['avg_duration_ms']:.0f} ms",
                    "llm":     data["total_llm_calls"],
                })
            ui.table(columns=cols, rows=rows, row_key="status").props("dense flat") \
                .classes("w-full")

            # Duration percentiles
            d = m.get("duration_ms", {})
            if d:
                with ui.row().classes("gap-4 text-sm text-gray-500"):
                    ui.label(f"p50: {d.get('p50', 0):.0f}ms")
                    ui.label(f"p95: {d.get('p95', 0):.0f}ms")
                    ui.label(f"p99: {d.get('p99', 0):.0f}ms")
                    ui.label(f"avg: {d.get('avg', 0):.0f}ms")

    except Exception as exc:
        ui.label(f"Pipeline metrics unavailable: {exc}").classes("text-gray-400 text-sm")

    ui.separator()

    # ── 2. LLM Cost ───────────────────────────────────────────
    ui.label("LLM Cost").classes("text-xl font-semibold text-gray-700")
    try:
        from rcm_denial.services.cost_tracker import get_batch_cost_summary
        cost = get_batch_cost_summary(batch_id=batch_id)

        if cost["total_calls"] == 0:
            ui.label("No LLM calls recorded yet.").classes("text-gray-400 italic")
        else:
            # Summary chips
            with ui.row().classes("gap-4"):
                _chip("Total Cost", f"${cost['total_cost_usd']:.6f}", "green")
                _chip("Total Calls", str(cost["total_calls"]), "blue")
                _chip("Claims Tracked", str(cost["claims_tracked"]), "blue")
                _chip("Avg/Claim", f"${cost['avg_cost_per_claim']:.6f}", "green")

            # By model table
            if cost.get("by_model"):
                cols = [
                    {"name": "model",  "label": "Model",         "field": "model"},
                    {"name": "calls",  "label": "Calls",         "field": "calls"},
                    {"name": "input",  "label": "Input Tokens",  "field": "input"},
                    {"name": "output", "label": "Output Tokens", "field": "output"},
                    {"name": "cost",   "label": "Cost (USD)",    "field": "cost"},
                ]
                rows = [
                    {
                        "model":  model,
                        "calls":  d["calls"],
                        "input":  f"{d['input_tokens']:,}",
                        "output": f"{d['output_tokens']:,}",
                        "cost":   f"${d['cost_usd']:.6f}",
                    }
                    for model, d in sorted(cost["by_model"].items())
                ]
                ui.table(columns=cols, rows=rows, row_key="model").props("dense flat") \
                    .classes("w-full")

    except Exception as exc:
        ui.label(f"Cost data unavailable: {exc}").classes("text-gray-400 text-sm")

    ui.separator()

    # ── 3. Review Queue ───────────────────────────────────────
    ui.label("Review Queue").classes("text-xl font-semibold text-gray-700")
    try:
        from rcm_denial.services.review_queue import get_review_stats
        rq = get_review_stats(batch_id=batch_id)

        total = rq.get("total_claims", 0)
        if total == 0:
            ui.label("No review queue data.").classes("text-gray-400 italic")
        else:
            with ui.row().classes("gap-4"):
                _chip("Total", str(total), "blue")
                _chip("Approved", str(rq.get("approved", 0)), "green")
                _chip("Pending", str(rq.get("pending", 0)), "orange")
                _chip("Written Off", str(rq.get("write_off_count", 0)), "red")

            # Status breakdown
            breakdown = rq.get("status_breakdown", {})
            if breakdown:
                with ui.row().classes("gap-3 flex-wrap"):
                    for status, count in sorted(breakdown.items()):
                        color = {"pending": "orange", "approved": "green", "submitted": "green",
                                 "re_routed": "blue", "human_override": "purple",
                                 "written_off": "red"}.get(status, "gray")
                        ui.badge(f"{status}: {count}", color=color)

            # Write-off impact
            wo_amount = rq.get("write_off_total_amount", 0)
            if wo_amount > 0:
                ui.label(f"Write-off revenue impact: ${wo_amount:,.2f}") \
                    .classes("text-red-600 font-semibold mt-2")

    except Exception as exc:
        ui.label(f"Review stats unavailable: {exc}").classes("text-gray-400 text-sm")

    ui.separator()

    # ── 4. Eval Quality Signals ────────────────────────────────
    ui.label("Eval Quality Signals").classes("text-xl font-semibold text-gray-700")
    try:
        from rcm_denial.services.review_queue import get_review_stats
        rq = get_review_stats(batch_id=batch_id)
        fp_rate = rq.get("first_pass_approval_rate_pct")
        ov_rate = rq.get("override_rate_pct")
        calib = rq.get("confidence_calibration", {})
        reroute = rq.get("reroute_by_stage", {})
        mc_ids = rq.get("multi_cycle_claim_ids", [])

        if fp_rate is None and ov_rate is None:
            ui.label("No eval data yet.").classes("text-gray-400 italic")
        else:
            with ui.row().classes("gap-4"):
                if fp_rate is not None:
                    color = "green" if fp_rate >= 70 else ("orange" if fp_rate >= 50 else "red")
                    _chip("First-Pass Approval", f"{fp_rate:.1f}%", color)
                if ov_rate is not None:
                    color = "green" if ov_rate <= 5 else ("orange" if ov_rate <= 15 else "red")
                    _chip("Override Rate", f"{ov_rate:.1f}%", color)
                if mc_ids:
                    _chip("Multi-Cycle Claims", str(len(mc_ids)), "orange")

            # Confidence calibration
            gap = calib.get("gap")
            if gap is not None:
                color = "green" if gap > 0 else "red"
                ui.label(f"Confidence calibration gap: {gap:+.3f}") \
                    .classes(f"text-{color}-600 text-sm mt-1")
                ui.label("Positive = well-calibrated (approved claims score higher than re-routed)") \
                    .classes("text-xs text-gray-400")

            # Reroute hotspots
            if reroute:
                ui.label("Re-route hotspots:").classes("text-sm font-semibold mt-2")
                for stage, cnt in sorted(reroute.items(), key=lambda x: -x[1]):
                    with ui.row().classes("gap-2 items-center"):
                        ui.label(stage).classes("text-sm text-gray-600 w-48")
                        ui.linear_progress(value=cnt / max(rq.get("total_claims", 1), 1)) \
                            .classes("w-48").props("color=orange")
                        ui.label(str(cnt)).classes("text-sm")

    except Exception:
        ui.label("Eval signals unavailable.").classes("text-gray-400 text-sm")


def _chip(label: str, value: str, color: str) -> None:
    with ui.card().classes(f"px-4 py-2 bg-{color}-50"):
        ui.label(value).classes(f"text-xl font-bold text-{color}-700")
        ui.label(label).classes("text-xs text-gray-500")
