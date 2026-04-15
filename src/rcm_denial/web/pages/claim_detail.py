##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/claim_detail.py
# Purpose: Claim detail page — full view of a single claim
#          including AI summary, analysis, appeal letter,
#          audit trail, PDF download, and action buttons.
#
##########################################################

from __future__ import annotations

import json
from pathlib import Path

from nicegui import ui

from rcm_denial.web.app import create_header, create_footer


@ui.page("/claim/{run_id}")
def claim_detail_page(run_id: str):
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return
    create_header()

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        try:
            from rcm_denial.services.review_queue import get_queue_item
            item = get_queue_item(run_id)
        except Exception as exc:
            ui.label(f"Error loading claim: {exc}").classes("text-red-600")
            create_footer()
            return

        if not item:
            ui.label(f"Claim not found: {run_id}").classes("text-red-600 text-xl")
            create_footer()
            return

        claim_id = item.get("claim_id", "")
        status = item.get("status", "unknown")

        # ── Header ────────────────────────────────────────────
        with ui.row().classes("items-center gap-4"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/review")) \
                .props("flat round")
            ui.label(f"Claim: {claim_id}").classes("text-3xl font-bold text-gray-800")
            _status_badge(status)

        # ── Key info cards ────────────────────────────────────
        with ui.row().classes("gap-4 flex-wrap"):
            _info_card("Billed Amount", f"${item.get('billed_amount', 0):,.2f}")
            _info_card("CARC Code", item.get("carc_code", "-"))
            _info_card("Category", item.get("denial_category", "-"))
            _info_card("Package Type", (item.get("package_type") or "-").upper())
            _info_card("Confidence", f"{item.get('confidence_score', 0):.0%}" if item.get("confidence_score") else "-")
            _info_card("Review Count", str(item.get("review_count", 0)))
            if item.get("is_urgent"):
                _info_card("Priority", "URGENT", color="red")

        # ── AI Summary ────────────────────────────────────────
        ui.label("AI Summary").classes("text-xl font-semibold text-gray-700 mt-4")
        summary = item.get("ai_summary", "No summary available")
        with ui.card().classes("w-full bg-gray-50"):
            ui.label(summary).classes("whitespace-pre-wrap text-sm font-mono text-gray-700 p-2")

        # ── State snapshot details ────────────────────────────
        state_json = item.get("state_snapshot")
        if state_json:
            try:
                state_data = json.loads(state_json)
                _render_state_details(state_data)
            except Exception:
                pass

        # ── PDF Download ──────────────────────────────────────
        pdf_path = item.get("pdf_package_path")
        output_dir = item.get("output_dir")
        if pdf_path or output_dir:
            ui.label("Documents").classes("text-xl font-semibold text-gray-700 mt-4")
            with ui.row().classes("gap-4"):
                if pdf_path and Path(pdf_path).exists():
                    # Serve via static /output mount
                    relative = Path(pdf_path).name
                    claim_dir = Path(pdf_path).parent.name
                    url = f"/output/{claim_dir}/{relative}"
                    ui.link(f"Download {relative}", url, new_tab=True) \
                        .classes("text-blue-600 underline")
                if output_dir and Path(output_dir).exists():
                    files = sorted(Path(output_dir).glob("*"))
                    for f in files:
                        if f.is_file():
                            claim_dir = Path(output_dir).name
                            url = f"/output/{claim_dir}/{f.name}"
                            ui.link(f.name, url, new_tab=True) \
                                .classes("text-sm text-blue-500")

        # ── Reviewer notes ────────────────────────────────────
        notes = item.get("reviewer_notes")
        override_text = item.get("override_response_text")
        wo_reason = item.get("write_off_reason")

        if notes or override_text or wo_reason:
            ui.label("Reviewer Actions").classes("text-xl font-semibold text-gray-700 mt-4")
            if notes:
                ui.label(f"Notes: {notes}").classes("text-sm text-gray-600")
            if override_text:
                with ui.card().classes("w-full bg-purple-50"):
                    ui.label("Human Override Text:").classes("text-sm font-semibold text-purple-700")
                    ui.label(override_text).classes("whitespace-pre-wrap text-sm")
            if wo_reason:
                ui.label(f"Write-off reason: {wo_reason}").classes("text-sm text-red-600")
                wo_notes = item.get("write_off_notes", "")
                if wo_notes:
                    ui.label(f"Justification: {wo_notes}").classes("text-sm text-gray-500")

        # ── Metadata ──────────────────────────────────────────
        with ui.expansion("Raw Metadata", icon="code").classes("w-full mt-4"):
            safe_item = {k: v for k, v in item.items() if k != "state_snapshot"}
            ui.code(json.dumps(safe_item, indent=2, default=str)).classes("w-full text-xs")

        ui.label(f"Run ID: {run_id}").classes("text-xs text-gray-400 mt-2")

    create_footer()


def _render_state_details(state: dict) -> None:
    """Render key sections from DenialWorkflowState."""
    # Denial analysis
    analysis = state.get("denial_analysis")
    if analysis:
        ui.label("Denial Analysis").classes("text-xl font-semibold text-gray-700 mt-4")
        with ui.card().classes("w-full"):
            with ui.row().classes("gap-6 text-sm"):
                ui.label(f"Root cause: {analysis.get('root_cause', '-')}").classes("text-gray-700")
            with ui.row().classes("gap-4 text-sm text-gray-500 mt-1"):
                ui.label(f"Action: {analysis.get('recommended_action', '-')}")
                ui.label(f"Category: {analysis.get('denial_category', '-')}")
                ui.label(f"Confidence: {analysis.get('confidence_score', 0):.0%}")
            reasoning = analysis.get("reasoning", "")
            if reasoning:
                with ui.expansion("Reasoning", icon="psychology").classes("w-full"):
                    ui.label(reasoning).classes("text-sm text-gray-600")

    # Evidence check
    evidence = state.get("evidence_check")
    if evidence:
        ui.label("Evidence Assessment").classes("text-xl font-semibold text-gray-700 mt-4")
        with ui.card().classes("w-full"):
            sufficient = evidence.get("evidence_sufficient", False)
            color = "green" if sufficient else "red"
            ui.label(f"Evidence sufficient: {sufficient}").classes(f"text-{color}-600 font-semibold")
            args = evidence.get("key_arguments", [])
            if args:
                ui.label("Key arguments:").classes("text-sm font-semibold mt-2")
                for arg in args:
                    ui.label(f"  - {arg}").classes("text-sm text-gray-600")
            gaps = evidence.get("evidence_gaps", [])
            if gaps:
                ui.label("Evidence gaps:").classes("text-sm font-semibold text-orange-600 mt-2")
                for gap in gaps:
                    ui.label(f"  - {gap}").classes("text-sm text-orange-500")

    # Appeal letter preview
    appeal = state.get("appeal_package")
    if appeal and appeal.get("appeal_letter"):
        letter = appeal["appeal_letter"]
        ui.label("Appeal Letter Preview").classes("text-xl font-semibold text-gray-700 mt-4")
        with ui.card().classes("w-full bg-blue-50"):
            ui.label(f"RE: {letter.get('subject_line', '')}").classes("font-semibold")
            for section in ("opening_paragraph", "denial_summary", "clinical_justification",
                            "regulatory_basis", "closing_paragraph"):
                text = letter.get(section, "")
                if text:
                    ui.label(text).classes("text-sm mt-2")

    # Audit trail
    audit_log = state.get("audit_log", [])
    if audit_log:
        ui.label("Audit Trail").classes("text-xl font-semibold text-gray-700 mt-4")
        with ui.timeline(side="right"):
            for entry in audit_log:
                node = entry.get("node_name", "unknown")
                entry_status = entry.get("status", "")
                details = entry.get("details", "")
                duration = entry.get("duration_ms")
                color = "green" if entry_status == "completed" else (
                    "red" if entry_status == "failed" else "blue"
                )
                subtitle = f"{duration:.0f}ms" if duration else ""
                body = details[:200] if details else ""
                ui.timeline_entry(
                    title=f"{node} — {entry_status}",
                    subtitle=subtitle,
                    body=body,
                    color=color,
                    icon="check_circle" if entry_status == "completed" else "error",
                )


def _info_card(label: str, value: str, color: str = "gray") -> None:
    with ui.card().classes(f"px-4 py-2 bg-{color}-50 min-w-[100px]"):
        ui.label(value).classes(f"text-lg font-bold text-{color}-700")
        ui.label(label).classes("text-xs text-gray-500")


def _status_badge(status: str) -> None:
    colors = {
        "pending": "orange", "approved": "green", "submitted": "green",
        "re_routed": "blue", "re_processed": "cyan",
        "human_override": "purple", "written_off": "red",
    }
    ui.badge(status.upper(), color=colors.get(status, "gray"))
