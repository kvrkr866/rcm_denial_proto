##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/review.py
# Purpose: Review Queue page — table of claims awaiting
#          review with action buttons (approve, re-route,
#          override, write-off).
#
##########################################################

from __future__ import annotations

from nicegui import ui

from rcm_denial.services.audit_service import get_logger
from rcm_denial.web.layout import create_header, create_footer

logger = get_logger(__name__)


STATUS_COLORS = {
    "pending":        "orange",
    "approved":       "green",
    "submitted":      "green",
    "re_routed":      "blue",
    "re_processed":   "cyan",
    "human_override": "purple",
    "written_off":    "red",
}

REENTRY_STAGES = [
    ("intake_agent",         "Intake (re-process from scratch)"),
    ("targeted_ehr_agent",   "Targeted EHR (fetch more clinical evidence)"),
    ("response_agent",       "Response (regenerate letter/plan)"),
]


@ui.page("/review")
def review_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        ui.label("Review Queue").classes("text-2xl font-bold text-gray-800")

        with ui.tabs().classes("w-full") as tabs:
            review_tab = ui.tab("Pending Review")
            submit_tab = ui.tab("Ready to Submit")
            submitted_tab = ui.tab("Submitted")

        with ui.tab_panels(tabs, value=review_tab).classes("w-full"):
            # ── TAB 1: Pending Review ─────────────────────────
            with ui.tab_panel(review_tab):
                _review_panel_content()

            # ── TAB 2: Ready to Submit (approved, awaiting submission) ──
            with ui.tab_panel(submit_tab):
                _submission_queue_panel()

            # ── TAB 3: Submitted (final state) ────────────────
            with ui.tab_panel(submitted_tab):
                _submitted_panel()

    create_footer()


def _submission_queue_panel():
    """Shows approved claims ready for payer submission with Submit buttons."""
    submit_container = ui.column().classes("w-full gap-2")

    async def refresh_submit_queue():
        submit_container.clear()
        try:
            from rcm_denial.services.review_queue import get_queue
            items = get_queue(status="approved", limit=100)
            if not items:
                with submit_container:
                    ui.label("No approved claims awaiting submission.") \
                        .classes("text-gray-400 italic py-4")
                return

            with submit_container:
                ui.label(f"{len(items)} claim(s) approved and ready for payer submission") \
                    .classes("text-sm text-green-600 font-semibold")

                for item in items:
                    _submit_queue_row(item, refresh_submit_queue)

        except Exception as exc:
            with submit_container:
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Refresh", icon="refresh", on_click=refresh_submit_queue) \
        .props("flat color=primary size=sm")
    ui.timer(0.1, refresh_submit_queue, once=True)


def _submit_queue_row(item: dict, refresh_callback) -> None:
    claim_id = item.get("claim_id", "")
    run_id = item.get("run_id", "")
    amount = item.get("billed_amount", 0)
    payer = item.get("denial_category", "")
    batch_id = item.get("batch_id", "")

    with ui.card().classes("w-full px-3 py-2 bg-green-50 border-l-4 border-green-500"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.link(claim_id, f"/claim/{run_id}") \
                    .classes("text-sm font-semibold text-blue-700 underline")
                ui.label(f"${amount:,.2f} | {payer} | Batch: {batch_id}") \
                    .classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                async def do_submit(rid=run_id, cid=claim_id):
                    try:
                        from rcm_denial.services.submission_service import submit_approved_claim
                        from rcm_denial.services.review_queue import get_queue_item

                        result = submit_approved_claim(rid)

                        if result.success:
                            # Record disposition + sync to EHR
                            try:
                                from rcm_denial.services.claim_disposition import (
                                    record_disposition, sync_to_ehr,
                                )
                                queue_item = get_queue_item(rid)
                                if queue_item:
                                    record_disposition(
                                        claim_id=cid,
                                        patient_id=queue_item.get("claim_id", ""),
                                        payer_id=payer or "",
                                        batch_id=batch_id or "",
                                        run_id=rid,
                                        billed_amount=amount,
                                        carc_code=queue_item.get("carc_code", ""),
                                        rarc_code="",
                                        denial_category=queue_item.get("denial_category", ""),
                                        disposition="resubmitted" if queue_item.get("package_type") == "resubmission" else "appealed",
                                        package_type=queue_item.get("package_type", ""),
                                        submission_method=result.submission_method,
                                        confirmation_number=result.confirmation_number,
                                    )
                                    sync_to_ehr(cid)
                            except Exception as disp_exc:
                                logger.warning("Disposition recording failed", error=str(disp_exc))

                            ui.notify(
                                f"Submitted: {cid} (conf: {result.confirmation_number}) — EHR updated",
                                type="positive",
                            )
                        else:
                            ui.notify(f"Submission failed: {result.error_detail}", type="negative")
                        await refresh_callback()
                    except Exception as exc:
                        ui.notify(f"Error: {exc}", type="negative")

                ui.button("Submit to Payer", icon="send",
                          on_click=do_submit) \
                    .props("color=green dense no-caps")


def _submitted_panel():
    """Shows claims that have been submitted to payer portals."""
    container = ui.column().classes("w-full gap-2")

    async def refresh():
        container.clear()
        try:
            from rcm_denial.services.review_queue import get_queue
            items = get_queue(status="submitted", limit=100)
            if not items:
                with container:
                    ui.label("No submitted claims yet.").classes("text-gray-400 italic py-4")
                return

            with container:
                ui.label(f"{len(items)} claim(s) submitted to payer portals") \
                    .classes("text-sm text-blue-600 font-semibold")
                for item in items:
                    claim_id = item.get("claim_id", "")
                    run_id = item.get("run_id", "")
                    amount = item.get("billed_amount", 0)
                    batch_id = item.get("batch_id", "")
                    with ui.card().classes("w-full px-3 py-1 bg-blue-50 border-l-4 border-blue-500"):
                        with ui.row().classes("items-center justify-between"):
                            with ui.column().classes("gap-0"):
                                ui.link(claim_id, f"/claim/{run_id}") \
                                    .classes("text-sm font-semibold text-blue-700 underline")
                                ui.label(f"${amount:,.2f} | Batch: {batch_id}") \
                                    .classes("text-xs text-gray-500")
                            ui.badge("SUBMITTED", color="blue")
        except Exception as exc:
            with container:
                ui.label(f"Error: {exc}").classes("text-red-600")

    ui.button("Refresh", icon="refresh", on_click=refresh).props("flat color=primary size=sm")
    ui.timer(0.1, refresh, once=True)


def _review_panel_content():
    """Original review queue content, now inside a tab."""
    # ── Filters ───────────────────────────────────────────
    page_state = {"current": 1, "page_size": 20}

    with ui.row().classes("gap-4 items-end"):
        status_filter = ui.select(
            label="Status",
            options=["all", "pending", "approved", "re_routed", "re_processed",
                     "human_override", "written_off", "submitted"],
            value="pending",
        ).classes("w-48")
        batch_filter = ui.input("Batch ID", placeholder="All batches").classes("w-48")

        # ── Queue table ───────────────────────────────────────
        table_container = ui.column().classes("w-full")
        pagination_container = ui.row().classes("w-full justify-center gap-4 items-center mt-2")

        async def refresh_queue():
            table_container.clear()
            pagination_container.clear()
            try:
                from rcm_denial.services.review_queue import get_queue, get_queue_count
                status = status_filter.value if status_filter.value != "all" else ""
                batch = batch_filter.value or ""
                page_size = page_state["page_size"]
                offset = (page_state["current"] - 1) * page_size

                total = get_queue_count(batch_id=batch, status=status)
                items = get_queue(
                    batch_id=batch, status=status,
                    limit=page_size, offset=offset,
                )
                total_pages = max(1, (total + page_size - 1) // page_size)

                if not items:
                    with table_container:
                        ui.label("No claims in queue matching filters.") \
                            .classes("text-gray-400 italic py-4")
                    return

                with table_container:
                    ui.label(f"{total} claim(s) total — page {page_state['current']} of {total_pages}") \
                        .classes("text-sm text-gray-500")

                    for item in items:
                        _queue_item_row(item, refresh_queue)

                # Pagination controls
                with pagination_container:
                    pagination_container.clear()

                    async def go_prev():
                        if page_state["current"] > 1:
                            page_state["current"] -= 1
                            await refresh_queue()

                    async def go_next():
                        if page_state["current"] < total_pages:
                            page_state["current"] += 1
                            await refresh_queue()

                    ui.button("Previous", icon="chevron_left", on_click=go_prev) \
                        .props(f"flat size=sm {'disable' if page_state['current'] <= 1 else ''}")
                    ui.label(f"Page {page_state['current']} / {total_pages}") \
                        .classes("text-sm text-gray-500")
                    ui.button("Next", icon="chevron_right", on_click=go_next) \
                        .props(f"flat size=sm {'disable' if page_state['current'] >= total_pages else ''}")

            except Exception as exc:
                with table_container:
                    ui.label(f"Error loading queue: {exc}").classes("text-red-600")

        ui.button("Refresh", icon="refresh", on_click=refresh_queue) \
            .props("flat color=primary size=sm")

        # Auto-load on page open
        ui.timer(0.1, refresh_queue, once=True)


def _queue_item_row(item: dict, refresh_callback) -> None:
    """Compact queue row — key info visible, details on expand/hover."""
    status = item.get("status", "unknown")
    claim_id = item.get("claim_id", "")
    run_id = item.get("run_id", "")
    amount = item.get("billed_amount", 0)
    carc = item.get("carc_code", "")
    category = item.get("denial_category", "")
    pkg_type = item.get("package_type", "")
    conf = item.get("confidence_score")
    review_count = item.get("review_count", 0)
    is_urgent = item.get("is_urgent", 0)

    color = STATUS_COLORS.get(status, "gray")

    # Compact single-row card
    with ui.card().classes(f"w-full px-3 py-2 border-l-4 border-{color}-500 mb-1"):
        # Main row: claim_id | carc | amount | status | actions — all on one line
        with ui.row().classes("w-full items-center gap-3"):
            if is_urgent:
                ui.icon("priority_high").classes("text-red-500 text-sm") \
                    .tooltip("Urgent — approaching deadline")

            ui.link(claim_id, f"/claim/{run_id}") \
                .classes("text-sm font-semibold text-blue-700 underline w-28") \
                .tooltip(f"Patient: {item.get('claim_id', '')} | Run: {run_id}")

            ui.label(f"CARC {carc}").classes("text-xs text-gray-500 w-16")

            ui.label(category[:12]).classes("text-xs text-gray-400 w-20") \
                .tooltip(f"Category: {category} | Package: {pkg_type}")

            if conf is not None:
                conf_color = "green" if conf >= 0.8 else ("orange" if conf >= 0.6 else "red")
                ui.label(f"{conf:.0%}").classes(f"text-xs text-{conf_color}-600 w-8") \
                    .tooltip(f"AI confidence: {conf:.1%}")

            ui.label(f"${amount:,.0f}").classes("text-sm font-mono text-gray-700 w-20 text-right")

            ui.badge(status[:8].upper(), color=color).classes("w-24")

            if review_count > 0:
                ui.badge(f"x{review_count}", color="blue").props("outline") \
                    .tooltip(f"{review_count} review cycle(s)")

            # Actions (compact icon buttons, only for actionable)
            if status in ("pending", "re_processed"):
                with ui.row().classes("gap-0 ml-auto"):
                    ui.button(icon="check",
                              on_click=lambda rid=run_id: _do_approve(rid, refresh_callback)) \
                        .props("flat dense round color=green size=xs").tooltip("Approve")
                    ui.button(icon="redo",
                              on_click=lambda rid=run_id: _show_reroute_dialog(rid, refresh_callback)) \
                        .props("flat dense round color=blue size=xs").tooltip("Re-route")
                    ui.button(icon="edit",
                              on_click=lambda rid=run_id: _show_override_dialog(rid, refresh_callback)) \
                        .props("flat dense round color=purple size=xs").tooltip("Override")
                    ui.button(icon="money_off",
                              on_click=lambda rid=run_id: _show_writeoff_dialog(rid, refresh_callback)) \
                        .props("flat dense round color=red size=xs").tooltip("Write-off")

        # Expandable details (hidden by default — saves screen space)
        with ui.expansion(icon="expand_more").classes("w-full").props("dense"):
            summary = item.get("ai_summary", "No summary available")
            ui.label(summary).classes("whitespace-pre-wrap text-xs font-mono text-gray-600 p-1")
            ui.label(f"Run: {run_id} | Pkg: {pkg_type} | Cycles: {review_count}") \
                .classes("text-xs text-gray-400")


async def _do_approve(run_id: str, refresh_callback) -> None:
    try:
        from rcm_denial.services.review_queue import approve
        approve(run_id, reviewer="web_ui")
        ui.notify(f"Approved: {run_id}", type="positive")
        await refresh_callback()
    except Exception as exc:
        ui.notify(f"Error: {exc}", type="negative")


def _show_reroute_dialog(run_id: str, refresh_callback) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Re-route Claim").classes("text-lg font-semibold")
        stage_select = ui.select(
            label="Re-entry stage",
            options={k: v for k, v in REENTRY_STAGES},
        ).classes("w-full")
        notes_input = ui.textarea("Reviewer notes (injected into LLM prompt)") \
            .classes("w-full")

        with ui.row().classes("gap-2 justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def do_reroute():
                try:
                    from rcm_denial.services.review_queue import re_route
                    re_route(run_id, stage=stage_select.value,
                             notes=notes_input.value, reviewer="web_ui")
                    ui.notify(f"Re-routed to {stage_select.value}", type="positive")
                    dialog.close()
                    await refresh_callback()
                except Exception as exc:
                    ui.notify(f"Error: {exc}", type="negative")

            ui.button("Re-route", icon="redo", on_click=do_reroute).props("color=primary")

    dialog.open()


def _show_override_dialog(run_id: str, refresh_callback) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[600px]"):
        ui.label("Human Override").classes("text-lg font-semibold")
        ui.label("Replace AI-generated response with your own text:") \
            .classes("text-sm text-gray-500")
        text_input = ui.textarea("Response text").classes("w-full").props("rows=10")

        with ui.row().classes("gap-2 justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def do_override():
                if not text_input.value.strip():
                    ui.notify("Response text cannot be empty", type="warning")
                    return
                try:
                    from rcm_denial.services.review_queue import human_override
                    human_override(run_id, response_text=text_input.value,
                                   reviewer="web_ui")
                    ui.notify("Override applied", type="positive")
                    dialog.close()
                    await refresh_callback()
                except Exception as exc:
                    ui.notify(f"Error: {exc}", type="negative")

            ui.button("Apply Override", icon="edit", on_click=do_override) \
                .props("color=purple")

    dialog.open()


def _show_writeoff_dialog(run_id: str, refresh_callback) -> None:
    reasons = [
        "timely_filing_expired",
        "cost_exceeds_recovery",
        "payer_non_negotiable",
        "duplicate_confirmed_paid",
        "patient_responsibility",
        "other",
    ]

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Write Off Claim").classes("text-lg font-semibold")
        ui.label("This action marks the claim as unrecoverable.") \
            .classes("text-sm text-orange-600")
        reason_select = ui.select(label="Reason", options=reasons).classes("w-full")
        notes_input = ui.input("Justification notes").classes("w-full")
        force_check = ui.checkbox("Force (bypass re-route guard)")

        with ui.row().classes("gap-2 justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def do_writeoff():
                try:
                    from rcm_denial.services.review_queue import write_off
                    write_off(run_id, reason=reason_select.value,
                              notes=notes_input.value, reviewer="web_ui",
                              force=force_check.value)
                    ui.notify("Claim written off", type="warning")
                    dialog.close()
                    await refresh_callback()
                except PermissionError as exc:
                    ui.notify(f"Blocked: {exc}", type="negative")
                except Exception as exc:
                    ui.notify(f"Error: {exc}", type="negative")

            ui.button("Write Off", icon="money_off", on_click=do_writeoff) \
                .props("color=red")

    dialog.open()
