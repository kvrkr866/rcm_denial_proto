##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/dashboard.py
# Purpose: Landing page -- overview cards with key metrics.
#
##########################################################

from __future__ import annotations

from nicegui import ui

from rcm_denial.web.layout import create_header, create_footer


@ui.page("/")
def dashboard_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        ui.label("Dashboard").classes("text-2xl font-bold text-gray-800")
        ui.label("Pipeline overview and key metrics").classes("text-sm text-gray-500 -mt-4")

        # ── Navigation cards (uniform size) ──────────────────────
        with ui.grid(columns=4).classes("w-full gap-4"):
            _metric_card("Pipeline",     "Process denied claims",         "play_circle", "/process", "blue")
            _metric_card("Review Queue", "Approve, re-route, write off",  "rate_review", "/review",  "orange")
            _metric_card("Statistics",   "Scorecard, LLM cost, impact",   "bar_chart",   "/stats",   "green")
            _metric_card("Evaluations",  "Golden dataset, quality signals","science",     "/evals",   "purple")

        # ── Live stats summary ────────────────────────────────────
        ui.separator()
        ui.label("Quick Stats").classes("text-lg font-semibold text-gray-700")

        stats_container = ui.column().classes("w-full")

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
                    with ui.grid(columns=6).classes("w-full gap-3"):
                        _stat_chip("Total Claims", str(total), "blue")
                        _stat_chip("Approved", str(approved), "green")
                        _stat_chip("Pending Review", str(pending), "orange")
                        _stat_chip("Written Off", str(written_off), "red")
                        if fp_rate is not None:
                            color = "green" if fp_rate >= 70 else ("orange" if fp_rate >= 50 else "red")
                            _stat_chip("First-Pass Approval", f"{fp_rate:.1f}%", color)
                        else:
                            _stat_chip("First-Pass Approval", "--", "gray")
                        if wo_amount > 0:
                            _stat_chip("Write-Off Impact", f"${wo_amount:,.2f}", "red")
                        else:
                            _stat_chip("Write-Off Impact", "$0", "green")
            except Exception:
                with stats_container:
                    stats_container.clear()
                    ui.label("No data yet -- process a batch first.") \
                        .classes("text-gray-400 italic")

        ui.timer(0.1, load_stats, once=True)

        # ── Clear History (dev/demo only) ──────────────────────
        ui.separator()
        with ui.row().classes("w-full items-center gap-4"):
            ui.label("Demo Tools").classes("text-sm font-semibold text-gray-500")

            async def clear_data():
                with ui.dialog() as confirm, ui.card().classes("w-80"):
                    ui.label("Clear All Demo Data?").classes("text-lg font-semibold")
                    ui.label(
                        "This will delete all processed claims, audit logs, "
                        "review queue items, submission logs, and output files. "
                        "SOP collections will be preserved."
                    ).classes("text-sm text-gray-600")
                    with ui.row().classes("gap-2 justify-end mt-4"):
                        ui.button("Cancel", on_click=confirm.close).props("flat")

                        async def do_clear():
                            try:
                                from rcm_denial.services.data_cleanup import clear_all_data
                                result = clear_all_data()
                                ui.notify(
                                    f"Cleared: {result.get('db_tables_cleared', 0)} tables, "
                                    f"{result.get('output_dirs_removed', 0)} output dirs",
                                    type="positive",
                                )
                                confirm.close()
                                await load_stats()
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")

                        ui.button("Clear All", icon="delete_forever",
                                  on_click=do_clear).props("color=red")
                confirm.open()

            ui.button("Clear History", icon="delete_sweep",
                      on_click=clear_data) \
                .props("flat color=red dense size=sm no-caps") \
                .tooltip("Delete all processed claims, logs, and output files")

    create_footer()


def _metric_card(title: str, description: str, icon: str, href: str, color: str) -> None:
    """Uniform-sized navigation card."""
    with ui.card().classes(
        "cursor-pointer hover:shadow-lg transition-shadow h-24 "
        "flex items-center justify-center"
    ).on("click", lambda: ui.navigate.to(href)):
        with ui.row().classes("items-center gap-3 px-4"):
            ui.icon(icon).classes(f"text-3xl text-{color}-600")
            with ui.column().classes("gap-0"):
                ui.label(title).classes("text-base font-semibold")
                ui.label(description).classes("text-[11px] text-gray-500 leading-tight")


def _stat_chip(label: str, value: str, color: str) -> None:
    """Uniform-sized stat chip."""
    with ui.card().classes(f"bg-{color}-50 h-20 flex items-center justify-center"):
        with ui.column().classes("items-center gap-0"):
            ui.label(value).classes(f"text-xl font-bold text-{color}-700")
            ui.label(label).classes("text-[10px] text-gray-500 text-center")
