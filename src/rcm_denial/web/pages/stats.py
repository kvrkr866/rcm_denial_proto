##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/stats.py
# Purpose: Operational Statistics dashboard — claim-centric
#          metrics for billing managers.
#
#          Technical metrics (LLM cost, tool performance,
#          fallback rates) are in Grafana, not here.
#
##########################################################

from __future__ import annotations

import sqlite3

from nicegui import ui

from rcm_denial.web.layout import create_header, create_footer


@ui.page("/stats")
def stats_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Operational Statistics").classes("text-2xl font-bold text-gray-800")
            with ui.row().classes("gap-3 items-end"):
                batch_input = ui.input("Batch ID", placeholder="All batches").classes("w-48") \
                    .props("dense outlined")
                refresh_btn = ui.button("Load", icon="refresh").props("color=primary dense")

        content = ui.column().classes("w-full gap-4")

        async def load_stats():
            content.clear()
            batch_id = batch_input.value or ""
            with content:
                try:
                    data = _query_all_stats(batch_id)
                    _render_stats(data, batch_id)
                except Exception as exc:
                    ui.label(f"Error loading stats: {exc}").classes("text-red-600")

        refresh_btn.on_click(load_stats)
        ui.timer(0.1, load_stats, once=True)

    create_footer()


# ──────────────────────────────────────────────────────────────────────
# Data query
# ──────────────────────────────────────────────────────────────────────

def _query_all_stats(batch_id: str = "") -> dict:
    from rcm_denial.config.settings import settings
    db_path = settings.data_dir / "rcm_denial.db"
    if not db_path.exists():
        return {}

    where_batch = "WHERE batch_id = ?" if batch_id else ""
    params = [batch_id] if batch_id else []
    data: dict = {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Claims intake
        try:
            row = conn.execute(
                f"SELECT COUNT(*) as total, "
                f"SUM(CASE WHEN status='valid' THEN 1 ELSE 0 END) as valid, "
                f"SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected "
                f"FROM claim_intake_log {where_batch}", params
            ).fetchone()
            data["intake"] = dict(row) if row else {"total": 0, "valid": 0, "rejected": 0}
        except Exception:
            data["intake"] = {"total": 0, "valid": 0, "rejected": 0}

        # Pipeline results
        try:
            rows = conn.execute(
                f"SELECT final_status, package_type, COUNT(*) as cnt, "
                f"AVG(duration_ms) as avg_ms, MIN(duration_ms) as min_ms, "
                f"MAX(duration_ms) as max_ms "
                f"FROM claim_pipeline_result {where_batch} "
                f"GROUP BY final_status, package_type", params
            ).fetchall()
            data["pipeline"] = [dict(r) for r in rows]
        except Exception:
            data["pipeline"] = []

        # Errors by stage
        try:
            rows = conn.execute(
                f"SELECT node_name, COUNT(*) as error_count "
                f"FROM claim_audit_log "
                f"{'WHERE batch_id = ? AND' if batch_id else 'WHERE'} status = 'failed' "
                f"GROUP BY node_name ORDER BY error_count DESC", params
            ).fetchall()
            data["errors_by_stage"] = [dict(r) for r in rows]
        except Exception:
            data["errors_by_stage"] = []

        # Human review outcomes
        try:
            rows = conn.execute(
                f"SELECT status, COUNT(*) as cnt, "
                f"SUM(billed_amount) as total_amount, "
                f"AVG(review_count) as avg_cycles "
                f"FROM human_review_queue {where_batch} "
                f"GROUP BY status", params
            ).fetchall()
            data["review_outcomes"] = {r["status"]: dict(r) for r in rows}
        except Exception:
            data["review_outcomes"] = {}

        # Per-CARC breakdown
        try:
            rows = conn.execute(
                f"SELECT carc_code, recommended_action, denial_category, "
                f"COUNT(*) as cnt "
                f"FROM claim_pipeline_result {where_batch} "
                f"GROUP BY carc_code, recommended_action, denial_category "
                f"ORDER BY cnt DESC", params
            ).fetchall()
            data["by_carc"] = [dict(r) for r in rows]
        except Exception:
            data["by_carc"] = []

        # Per-category with amounts (from review queue which has billed_amount)
        try:
            rows = conn.execute(
                f"SELECT denial_category, COUNT(*) as cnt, "
                f"SUM(billed_amount) as total_amount "
                f"FROM human_review_queue {where_batch} "
                f"GROUP BY denial_category ORDER BY total_amount DESC", params
            ).fetchall()
            data["by_category"] = [dict(r) for r in rows]
        except Exception:
            data["by_category"] = []

        # By routing action
        try:
            rows = conn.execute(
                f"SELECT COALESCE(routing_decision, 'unknown') as action, "
                f"COUNT(*) as cnt, SUM(billed_amount) as total_amount "
                f"FROM human_review_queue {where_batch} "
                f"GROUP BY routing_decision ORDER BY total_amount DESC", params
            ).fetchall()
            data["by_routing"] = [dict(r) for r in rows]
        except Exception:
            data["by_routing"] = []

        # Submissions
        try:
            rows = conn.execute(
                f"SELECT status, COUNT(*) as cnt "
                f"FROM submission_log {where_batch} "
                f"GROUP BY status", params
            ).fetchall()
            data["submissions"] = {r["status"]: r["cnt"] for r in rows}
        except Exception:
            data["submissions"] = {}

        # Processing time
        try:
            row = conn.execute(
                f"SELECT AVG(duration_ms) as avg_ms, "
                f"MIN(duration_ms) as min_ms, "
                f"MAX(duration_ms) as max_ms, "
                f"COUNT(*) as total "
                f"FROM claim_pipeline_result {where_batch}", params
            ).fetchone()
            data["timing"] = dict(row) if row else {}
        except Exception:
            data["timing"] = {}

        # Write-offs
        try:
            rows = conn.execute(
                f"SELECT write_off_reason, COUNT(*) as cnt, "
                f"SUM(billed_amount) as amount "
                f"FROM human_review_queue "
                f"{'WHERE batch_id = ? AND' if batch_id else 'WHERE'} status = 'written_off' "
                f"GROUP BY write_off_reason", params
            ).fetchall()
            data["write_offs"] = [dict(r) for r in rows]
        except Exception:
            data["write_offs"] = []

    return data


# ──────────────────────────────────────────────────────────────────────
# Render
# ──────────────────────────────────────────────────────────────────────

def _render_stats(data: dict, batch_id: str) -> None:
    if not data:
        ui.label("No data found. Process a batch first.").classes("text-gray-400 italic")
        return

    label = f"Batch: {batch_id}" if batch_id else "All Batches"
    ui.label(label).classes("text-sm text-gray-500")

    intake = data.get("intake", {})
    timing = data.get("timing", {})
    review = data.get("review_outcomes", {})
    submissions = data.get("submissions", {})

    total_loaded = intake.get("total", 0)
    total_processed = timing.get("total", 0)
    total_approved = review.get("approved", {}).get("cnt", 0) + review.get("submitted", {}).get("cnt", 0)
    total_pending = review.get("pending", {}).get("cnt", 0) + review.get("re_processed", {}).get("cnt", 0)
    total_rerouted = review.get("re_routed", {}).get("cnt", 0)
    total_writeoff = review.get("written_off", {}).get("cnt", 0)
    total_submitted = submissions.get("submitted", 0)
    total_sub_failed = submissions.get("failed", 0)
    total_amount = sum(v.get("total_amount", 0) or 0 for v in review.values())

    # ── Row 1: KPI cards ──────────────────────────────────────
    with ui.grid(columns=5).classes("w-full gap-3"):
        _kpi("Claims Loaded", str(total_loaded), "blue")
        _kpi("Processed", str(total_processed), "blue")
        _kpi("Approved", str(total_approved), "green")
        _kpi("Pending Review", str(total_pending), "orange")
        _kpi("Billed Amount at Risk", f"${total_amount:,.0f}", "blue")

    with ui.grid(columns=5).classes("w-full gap-3 mt-1"):
        _kpi("Re-routed (loop)", str(total_rerouted), "cyan")
        _kpi("Awaiting Submission", str(review.get("approved", {}).get("cnt", 0)), "orange")
        _kpi("Submitted to Payer", str(total_submitted), "green")
        _kpi("Submission Failed", str(total_sub_failed), "red" if total_sub_failed else "green")
        _kpi("Written Off", str(total_writeoff), "red" if total_writeoff else "green")

    ui.separator().classes("mt-2")

    # ── Row 2: Two columns ────────────────────────────────────
    with ui.row().classes("w-full gap-4"):

        # LEFT: Processing time + CARC breakdown
        with ui.column().classes("flex-1 gap-3"):
            ui.label("Processing Time").classes("text-sm font-semibold text-gray-700")
            if timing and timing.get("avg_ms"):
                avg_ms = timing.get("avg_ms") or 0
                min_ms = timing.get("min_ms") or 0
                max_ms = timing.get("max_ms") or 0
                with ui.grid(columns=3).classes("w-full gap-2"):
                    _kpi("Average", f"{avg_ms / 1000:.1f}s", "blue")
                    _kpi("Fastest", f"{min_ms / 1000:.1f}s", "green")
                    _kpi("Slowest", f"{max_ms / 1000:.1f}s", "orange")
            else:
                ui.label("No timing data yet.").classes("text-gray-400 italic text-sm")

            # CARC code breakdown
            carc_data = data.get("by_carc", [])
            if carc_data:
                ui.label("Claims by CARC Code").classes("text-sm font-semibold text-gray-700 mt-3")
                cols = [
                    {"name": "carc", "label": "CARC", "field": "carc"},
                    {"name": "action", "label": "Action", "field": "action"},
                    {"name": "category", "label": "Category", "field": "category"},
                    {"name": "count", "label": "Claims", "field": "count", "sortable": True},
                ]
                rows = [{
                    "carc": f"CARC {r.get('carc_code', '?')}",
                    "action": (r.get("recommended_action") or "?").upper(),
                    "category": (r.get("denial_category") or "?").replace("_", " ").title(),
                    "count": r["cnt"],
                } for r in carc_data]
                ui.table(columns=cols, rows=rows, row_key="carc") \
                    .props("dense flat").classes("w-full")

        # RIGHT: Review outcomes + errors
        with ui.column().classes("flex-1 gap-3"):
            ui.label("Human Review Outcomes").classes("text-sm font-semibold text-gray-700")
            if review:
                cols = [
                    {"name": "status", "label": "Status", "field": "status"},
                    {"name": "count", "label": "Claims", "field": "count", "sortable": True},
                    {"name": "amount", "label": "Billed Amount", "field": "amount"},
                    {"name": "cycles", "label": "Avg Cycles", "field": "cycles"},
                ]
                rows = [{
                    "status": status.replace("_", " ").upper(),
                    "count": v.get("cnt", 0),
                    "amount": f"${(v.get('total_amount') or 0):,.0f}",
                    "cycles": f"{(v.get('avg_cycles') or 0):.1f}",
                } for status, v in sorted(review.items())]
                ui.table(columns=cols, rows=rows, row_key="status") \
                    .props("dense flat").classes("w-full")
            else:
                ui.label("No review data yet.").classes("text-gray-400 italic text-sm")

            # Errors
            errors = data.get("errors_by_stage", [])
            if errors:
                ui.label("Errors by Pipeline Stage").classes("text-sm font-semibold text-red-600 mt-3")
                for e in errors:
                    with ui.row().classes("gap-2 items-center"):
                        ui.label(e["node_name"].replace("_", " ").title()).classes("text-xs text-gray-600 w-40")
                        ui.badge(str(e["error_count"]), color="red").props("dense")

    ui.separator().classes("mt-2")

    # ── Row 3: Categories + Write-offs ────────────────────────
    with ui.row().classes("w-full gap-4"):
        # Denial categories with amounts
        with ui.column().classes("flex-1 gap-2"):
            ui.label("Denial Categories").classes("text-sm font-semibold text-gray-700")
            categories = data.get("by_category", [])
            if categories:
                cols = [
                    {"name": "cat", "label": "Category", "field": "cat"},
                    {"name": "count", "label": "Claims", "field": "count", "sortable": True},
                    {"name": "amount", "label": "Total Amount", "field": "amount"},
                ]
                rows = [{
                    "cat": (r.get("denial_category") or "unknown").replace("_", " ").title(),
                    "count": r["cnt"],
                    "amount": f"${(r.get('total_amount') or 0):,.0f}",
                } for r in categories]
                ui.table(columns=cols, rows=rows, row_key="cat") \
                    .props("dense flat").classes("w-full")

        # Write-offs + Recovery rate
        with ui.column().classes("flex-1 gap-2"):
            ui.label("Write-Off Revenue Impact").classes("text-sm font-semibold text-gray-700")
            write_offs = data.get("write_offs", [])
            if write_offs:
                total_wo = sum(r.get("amount", 0) or 0 for r in write_offs)
                ui.label(f"Total Lost: ${total_wo:,.2f}").classes("text-lg font-bold text-red-600")
                cols = [
                    {"name": "reason", "label": "Reason", "field": "reason"},
                    {"name": "count", "label": "Claims", "field": "count"},
                    {"name": "amount", "label": "Amount", "field": "amount"},
                ]
                rows = [{
                    "reason": r["write_off_reason"].replace("_", " ").title(),
                    "count": r["cnt"],
                    "amount": f"${(r.get('amount') or 0):,.0f}",
                } for r in write_offs]
                ui.table(columns=cols, rows=rows, row_key="reason") \
                    .props("dense flat").classes("w-full")
            else:
                ui.label("No write-offs -- $0 revenue lost").classes("text-green-600 text-sm")

            # Recovery rate
            if total_processed > 0:
                recovery = total_approved + total_submitted
                rate = recovery / total_processed * 100
                color = "green" if rate >= 70 else ("orange" if rate >= 50 else "red")
                ui.label("Recovery Rate").classes("text-sm font-semibold text-gray-700 mt-3")
                ui.label(f"{rate:.1f}%").classes(f"text-2xl font-bold text-{color}-600")
                ui.label(f"{recovery} of {total_processed} claims recovered") \
                    .classes("text-xs text-gray-500")

    # ── Row 4: Routing breakdown ──────────────────────────────
    routing = data.get("by_routing", [])
    if routing:
        ui.separator().classes("mt-2")
        ui.label("Claims by Recommended Action").classes("text-sm font-semibold text-gray-700")
        with ui.grid(columns=4).classes("w-full gap-2"):
            for r in routing:
                action = (r.get("action") or "unknown").upper()
                color = {"RESUBMIT": "blue", "APPEAL": "orange", "BOTH": "purple",
                         "WRITE_OFF": "red"}.get(action, "gray")
                _kpi(action, f"{r['cnt']} (${(r.get('total_amount') or 0):,.0f})", color)


def _kpi(label: str, value: str, color: str) -> None:
    with ui.card().classes(f"bg-{color}-50 h-16 flex items-center justify-center px-3"):
        with ui.column().classes("items-center gap-0"):
            ui.label(value).classes(f"text-lg font-bold text-{color}-700")
            ui.label(label).classes("text-[10px] text-gray-500 text-center leading-tight")
