##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/dashboard.py
# Purpose: Landing page — overview cards with key metrics.
#
##########################################################

from __future__ import annotations

from nicegui import ui

from rcm_denial.web.app import create_header, create_footer


@ui.page("/")
def dashboard_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        ui.label("Dashboard").classes("text-3xl font-bold text-gray-800")
        ui.label("Pipeline overview and key metrics").classes("text-gray-500")

        # ── Metric cards ──────────────────────────────────────────
        with ui.row().classes("w-full gap-4 flex-wrap"):
            _metric_card("Pipeline",      "Process denied claims through the AI pipeline",  "play_circle",  "/process",  "blue")
            _metric_card("Review Queue",  "Approve, re-route, or write off claims",         "rate_review",  "/review",   "orange")
            _metric_card("Statistics",    "Pipeline scorecard, LLM cost, write-off impact", "bar_chart",    "/stats",    "green")
            _metric_card("Evaluations",   "Golden dataset regression, quality signals",     "science",      "/evals",    "purple")

        # ── Live stats summary ────────────────────────────────────
        ui.separator()
        ui.label("Quick Stats").classes("text-xl font-semibold text-gray-700")

        stats_container = ui.column().classes("w-full gap-2")

        async def load_stats():
            try:
                from rcm_denial.services.review_queue import get_review_stats
                s = get_review_stats()
                total = s.get("total_claims", 0)
                approved = s.get("approved", 0)
                pending = s.get("pending", 0)
                written_off = s.get("write_off_count", 0)
                fp_rate = s.get("first_pass_approval_rate_pct", None)
                wo_amount = s.get("write_off_total_amount", 0)

                with stats_container:
                    stats_container.clear()
                    with ui.row().classes("gap-6 flex-wrap"):
                        _stat_chip("Total Claims", str(total), "blue")
                        _stat_chip("Approved", str(approved), "green")
                        _stat_chip("Pending Review", str(pending), "orange")
                        _stat_chip("Written Off", str(written_off), "red")
                        if fp_rate is not None:
                            color = "green" if fp_rate >= 70 else ("orange" if fp_rate >= 50 else "red")
                            _stat_chip("First-Pass Approval", f"{fp_rate:.1f}%", color)
                        if wo_amount > 0:
                            _stat_chip("Write-Off Impact", f"${wo_amount:,.2f}", "red")
            except Exception as exc:
                with stats_container:
                    stats_container.clear()
                    ui.label(f"No data yet — process a batch first.").classes("text-gray-400 italic")

        ui.timer(0.1, load_stats, once=True)

    create_footer()


def _metric_card(title: str, description: str, icon: str, href: str, color: str) -> None:
    with ui.card().classes("cursor-pointer hover:shadow-lg transition-shadow w-64") \
            .on("click", lambda: ui.navigate.to(href)):
        with ui.row().classes("items-center gap-3"):
            ui.icon(icon).classes(f"text-3xl text-{color}-600")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("text-lg font-semibold")
                ui.label(description).classes("text-xs text-gray-500")


def _stat_chip(label: str, value: str, color: str) -> None:
    with ui.card().classes(f"px-4 py-2 bg-{color}-50"):
        ui.label(value).classes(f"text-2xl font-bold text-{color}-700")
        ui.label(label).classes("text-xs text-gray-500")
