##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/pages/process.py
# Purpose: Process Claims — three-panel operator console.
#
#          Left:   Pending claims queue (waiting to process)
#          Center: Currently processing claim + live pipeline stepper
#          Right:  Completed claims with status + action buttons
#          Bottom: Upload CSV or add single claim
#
#          All operations from web — no CLI needed.
#
##########################################################

from __future__ import annotations

import asyncio
import csv
import io
import sqlite3
import tempfile
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from nicegui import ui, events

from rcm_denial.web.layout import create_header, create_footer


# Pipeline stages in order
PIPELINE_STAGES = [
    ("intake_agent",             "Intake"),
    ("enrichment_agent",         "Enrich"),
    ("analysis_agent",           "Analysis"),
    ("evidence_check_agent",     "Evidence"),
    ("targeted_ehr_agent",       "EHR Fetch"),
    ("response_agent",           "Response"),
    ("correction_plan_agent",    "Correction"),
    ("appeal_prep_agent",        "Appeal"),
    ("document_packaging_agent", "Package"),
    ("review_gate_agent",        "Review Gate"),
]


# ──────────────────────────────────────────────────────────────────────
# Shared state for the three-panel view
# ──────────────────────────────────────────────────────────────────────

class BatchState:
    """Tracks batch processing state for the three-panel UI."""
    def __init__(self):
        self.pending: list[dict] = []       # claims waiting to process
        self.selected: set[str] = set()     # claim_ids selected via checkbox
        self.current: dict | None = None    # claim currently being processed
        self.current_stage: str = ""        # current pipeline node name
        self.completed: list[dict] = []     # finished claims with results
        self.is_running: bool = False
        self.cancel_requested: bool = False # stop after current claim
        self.batch_id: str = ""
        self.batch_seq: int = 1             # auto-increment per upload


# Module-level persistent state — survives page navigation
_session_state: dict[str, BatchState] = {}


def _get_session_state() -> BatchState:
    """Get or create session-scoped BatchState that survives page navigation."""
    from nicegui import app
    session_id = app.storage.user.get("_session_id", "default")
    if session_id not in _session_state:
        _session_state[session_id] = BatchState()
    return _session_state[session_id]


@ui.page("/process")
def process_page():
    from rcm_denial.web.auth import require_auth
    if not require_auth():
        return

    from nicegui import app
    # Ensure session ID exists
    if "_session_id" not in app.storage.user:
        app.storage.user["_session_id"] = f"s-{datetime.now().strftime('%H%M%S')}"

    create_header()

    state = _get_session_state()
    file_state = {"name": app.storage.user.get("_csv_filename", None)}

    with ui.column().classes("w-full max-w-7xl mx-auto px-4 py-1 gap-1") \
            .style("height: calc(100vh - 80px)"):

        # ── Init SOPs: top-right, before everything else ──────
        with ui.row().classes("w-full justify-end flex-shrink-0"):
            ui.button("Init SOPs", icon="build",
                      on_click=lambda: _run_sop_init(center_container)) \
                .props("flat color=grey dense size=sm no-caps") \
                .tooltip("Build/refresh SOP RAG collections for all payers")

        # ── Claim(s) as Input ─────────────────────────────────
        with ui.card().classes("w-full py-1 px-3 flex-shrink-0"):
            ui.label("Claim(s) as Input").classes("text-xs font-semibold text-gray-600 mb-1")
            with ui.row().classes("w-full gap-3 items-center"):
                # CSV upload: compact button with tick/status
                upload_icon = ui.icon("cloud_upload").classes("text-gray-400 text-lg")
                upload_label = ui.button(
                    "Upload CSV", icon="attach_file",
                    on_click=lambda: upload_hidden.run_method("pickFiles"),
                ).props("flat dense no-caps color=primary size=sm")

                # Hidden actual upload widget
                upload_hidden = ui.upload(
                    on_upload=lambda e: _handle_upload_v2(
                        e, state, pending_container, pending_count,
                        batch_id_input, upload_icon, upload_label, file_state,
                    ),
                    auto_upload=True,
                ).classes("hidden").props('accept=".csv"')

                ui.separator().props("vertical").classes("h-6")

                # Process mode toggle
                process_mode = ui.toggle(
                    {False: "All Claims", True: "Selected Only"},
                    value=False,
                ).props("dense size=sm no-caps")

                process_btn = ui.button(
                    "Process", icon="play_arrow",
                    on_click=lambda: _run_batch(
                        state, pending_container, pending_count,
                        center_container, completed_container, completed_count,
                        batch_id_input, process_btn, cancel_btn,
                        selected_only=process_mode.value,
                    ),
                ).props("color=primary dense no-caps")

                cancel_btn = ui.button(
                    "Stop", icon="stop",
                    on_click=lambda: _request_cancel(state, cancel_btn),
                ).props("color=red dense outline no-caps")
                cancel_btn.set_visibility(False)

                batch_id_input = ui.input(placeholder="Batch ID") \
                    .classes("w-40").props("dense outlined")
                batch_id_input.value = f"B-{datetime.now().strftime('%y%m%d-%H%M')}-001"

        # ── Claim(s) Processing Progress (title for the three panels) ──
        ui.label("Claim(s) Processing Progress") \
            .classes("text-sm font-bold text-gray-800 flex-shrink-0 mt-1")

        # ── THREE-PANEL layout (fills remaining viewport) ─────
        with ui.row().classes("w-full gap-2 flex-1 min-h-0"):

            # LEFT: Pending queue
            with ui.card().classes("w-1/5 flex-shrink-0 flex flex-col p-2"):
                with ui.row().classes("items-center justify-between"):
                    ui.label("Pending").classes("text-xs font-semibold text-orange-700")
                    pending_count = ui.label("0").classes("text-[10px] text-gray-400")
                pending_container = ui.column() \
                    .classes("w-full gap-1 overflow-auto flex-1")

            # CENTER: Processing
            with ui.card().classes("flex-1 flex flex-col p-2"):
                ui.label("Processing").classes("text-xs font-semibold text-blue-700")
                center_container = ui.column() \
                    .classes("w-full gap-1 overflow-auto flex-1")

            # RIGHT: Completed
            with ui.card().classes("w-1/4 flex-shrink-0 flex flex-col p-2"):
                with ui.row().classes("items-center justify-between"):
                    ui.label("Completed").classes("text-xs font-semibold text-green-700")
                    completed_count = ui.label("0").classes("text-[10px] text-gray-400")
                completed_container = ui.column() \
                    .classes("w-full gap-1 overflow-auto flex-1")

        # ── Restore state if returning to this page ───────────
        if state.pending:
            _rebuild_pending_panel(state, pending_container, pending_count)
            # Restore upload indicator
            if file_state.get("name"):
                upload_icon._props["name"] = "check_circle"
                upload_icon.classes(replace="text-green-600 text-lg")
                upload_icon.update()
                upload_label.text = f"{len(state.pending)} claims"
                upload_label.update()

        if state.completed:
            completed_container.clear()
            with completed_container:
                for c in state.completed:
                    _completed_claim_card(c)
            completed_count.set_text(str(len(state.completed)))

    create_footer()


# ──────────────────────────────────────────────────────────────────────
# Cancel handler
# ──────────────────────────────────────────────────────────────────────

def _request_cancel(state: BatchState, cancel_btn):
    state.cancel_requested = True
    cancel_btn.props("disable")
    cancel_btn.text = "Cancelling..."
    ui.notify("Cancel requested — will stop after current claim finishes", type="warning")


# ──────────────────────────────────────────────────────────────────────
# Upload handler
# ──────────────────────────────────────────────────────────────────────

async def _handle_upload_v2(
    e: events.UploadEventArguments,
    state: BatchState,
    pending_container, pending_count,
    batch_id_input,
    upload_icon, upload_label, file_state,
):
    """Handle CSV upload: update icon to tick, show filename on hover."""
    # Handle different NiceGUI versions:
    #   v2.x: e.content (file-like), e.name
    #   v3.x: e.file (FileUpload with async read/text), e.file.name
    content = None
    filename = "uploaded.csv"

    try:
        if hasattr(e, "file") and e.file is not None:
            # NiceGUI 3.x — e.file is FileUpload (async)
            f = e.file
            filename = getattr(f, "name", filename)
            if hasattr(f, "text"):
                # async text() method
                content = await f.text("utf-8")
            elif hasattr(f, "read"):
                raw = await f.read()
                content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        elif hasattr(e, "content"):
            # NiceGUI 2.x — e.content is file-like or bytes
            if hasattr(e.content, "read"):
                content = e.content.read().decode("utf-8")
            elif isinstance(e.content, bytes):
                content = e.content.decode("utf-8")
            filename = getattr(e, "name", filename)
    except Exception as exc:
        ui.notify(f"Upload read error: {exc}", type="negative")
        return

    if not content:
        ui.notify("Upload failed — could not read file content", type="negative")
        return

    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    tmp.write(content)
    tmp.close()
    state._csv_path = tmp.name
    file_state["name"] = filename
    upload_icon._props["name"] = "check_circle"
    upload_icon.classes(replace="text-green-600 text-lg")
    upload_icon.update()
    upload_label.text = f"{len(rows)} claims"
    upload_label.tooltip(f"File: {filename}")
    upload_label.update()

    # Clear old list and populate with new CSV
    state.pending = rows
    state.selected = set()
    state.completed = []

    # Increment batch sequence and generate new batch ID
    state.batch_seq += 1
    batch_id_input.value = f"B-{datetime.now().strftime('%y%m%d-%H%M%S')}-{state.batch_seq:03d}"

    # Persist filename in session storage for page navigation
    try:
        from nicegui import app
        app.storage.user["_csv_filename"] = filename
    except Exception:
        pass

    _rebuild_pending_panel(state, pending_container, pending_count)
    ui.notify(f"Loaded {len(rows)} claims from {filename} (previous list cleared)", type="positive")


async def _handle_upload(
    e: events.UploadEventArguments,
    state: BatchState,
    pending_container, pending_count, batch_id_input,
):
    """Legacy fallback upload handler."""
    content = e.content.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    tmp.write(content)
    tmp.close()
    state._csv_path = tmp.name

    state.pending = rows
    state.selected = set()
    state.completed = []

    _rebuild_pending_panel(state, pending_container, pending_count)
    ui.notify(f"Loaded {len(rows)} claims", type="positive")


def _rebuild_pending_panel(state: BatchState, pending_container, pending_count) -> None:
    pending_container.clear()
    with pending_container:
        # Select all / none
        with ui.row().classes("gap-2 mb-1"):
            ui.button("All", on_click=lambda: _select_all(state, pending_container, pending_count)) \
                .props("flat dense size=xs color=primary").tooltip("Select all")
            ui.button("None", on_click=lambda: _select_none(state, pending_container, pending_count)) \
                .props("flat dense size=xs").tooltip("Deselect all")

        for row in state.pending:
            _pending_claim_chip(row, state)

    count = len(state.pending)
    sel = len(state.selected)
    pending_count.set_text(f"{count} claims" + (f" ({sel} selected)" if sel > 0 else ""))


def _select_all(state, pending_container, pending_count):
    state.selected = {r.get("claim_id", "") for r in state.pending}
    _rebuild_pending_panel(state, pending_container, pending_count)


def _select_none(state, pending_container, pending_count):
    state.selected = set()
    _rebuild_pending_panel(state, pending_container, pending_count)


def _pending_claim_chip(row: dict, state: BatchState) -> None:
    claim_id = row.get("claim_id", "?")
    payer = row.get("payer_id", "?")
    amount = row.get("billed_amount", "0")
    carc = row.get("carc_code", "?")
    is_selected = claim_id in state.selected

    with ui.card().classes(
        f"w-full px-3 py-1 cursor-pointer "
        f"{'bg-blue-50 border border-blue-300' if is_selected else 'bg-orange-50'}"
    ):
        with ui.row().classes("items-center gap-2"):
            cb = ui.checkbox(value=is_selected, on_change=lambda e, cid=claim_id: (
                state.selected.add(cid) if e.value else state.selected.discard(cid)
            )).props("dense size=xs")
            with ui.column().classes("gap-0 flex-1"):
                ui.label(claim_id).classes("text-sm font-semibold")
                ui.label(f"{payer} | CARC {carc}").classes("text-[10px] text-gray-500")
            ui.label(f"${float(amount):,.0f}").classes("text-xs font-mono text-gray-600")


# ──────────────────────────────────────────────────────────────────────
# Process batch — claim by claim with live updates
# ──────────────────────────────────────────────────────────────────────

async def _run_batch(
    state: BatchState,
    pending_container, pending_count,
    center_container, completed_container, completed_count,
    batch_id_input, process_btn, cancel_btn,
    selected_only: bool = False,
):
    if not state.pending:
        ui.notify("Upload a CSV first", type="warning")
        return

    if state.is_running:
        ui.notify("Batch already running", type="warning")
        return

    if selected_only and not state.selected:
        ui.notify("Select at least one claim first (use checkboxes)", type="warning")
        return

    state.is_running = True
    state.cancel_requested = False
    process_btn.props("disable loading")
    cancel_btn.set_visibility(True)
    cancel_btn.props(remove="disable")
    cancel_btn.text = "Cancel"
    # Auto-generate unique batch ID: date + time + sequence
    if batch_id_input.value and not batch_id_input.value.startswith("B-"):
        state.batch_id = batch_id_input.value
    else:
        state.batch_id = f"B-{datetime.now().strftime('%y%m%d-%H%M%S')}-{len(state.completed) + 1:03d}"
    batch_id_input.value = state.batch_id

    try:
        from rcm_denial.services.claim_intake import stream_claims
        csv_path = getattr(state, "_csv_path", None)
        if not csv_path:
            ui.notify("No CSV file loaded", type="warning")
            return

        all_claims = list(stream_claims(csv_path, source="passthrough", batch_id=state.batch_id))

        # Filter to selected claims only if requested
        if selected_only:
            claims = [c for c in all_claims if c.claim_id in state.selected]
        else:
            claims = all_claims
        total = len(claims)

        if total == 0:
            ui.notify("No claims to process", type="warning")
            return

        for idx, claim in enumerate(claims):
            claim_id = claim.claim_id
            claim_info = {
                "claim_id": claim_id,
                "payer_id": claim.payer_id,
                "carc_code": claim.carc_code,
                "billed_amount": claim.billed_amount,
                "patient_name": claim.patient_name or claim.patient_id,
            }
            state.current = claim_info

            # Update pending panel — remove current claim
            pending_container.clear()
            remaining = claims[idx + 1:]
            with pending_container:
                for rc in remaining:
                    _pending_claim_chip({
                        "claim_id": rc.claim_id,
                        "payer_id": rc.payer_id,
                        "carc_code": rc.carc_code,
                        "billed_amount": str(rc.billed_amount),
                    }, state)
            pending_count.set_text(f"{len(remaining)} claims")

            # Update center panel — show current claim + stepper
            center_container.clear()
            stage_icons: dict[str, ui.icon] = {}
            stage_labels: dict[str, ui.label] = {}

            with center_container:
                # Claim header
                with ui.row().classes("items-center gap-3"):
                    ui.spinner("dots", size="lg").bind_visibility_from(state, "is_running")
                    with ui.column().classes("gap-0"):
                        ui.label(f"{claim_id}").classes("text-xl font-bold")
                        ui.label(
                            f"{claim_info['patient_name']} | {claim_info['payer_id']} | "
                            f"CARC {claim_info['carc_code']} | ${claim_info['billed_amount']:,.2f}"
                        ).classes("text-sm text-gray-500")

                ui.label(f"Claim {idx + 1} of {total}").classes("text-xs text-gray-400")

                # Pipeline stepper
                with ui.row().classes("gap-1 flex-wrap items-center mt-4"):
                    for i, (node_name, display_name) in enumerate(PIPELINE_STAGES):
                        with ui.column().classes("items-center w-16"):
                            icon = ui.icon("radio_button_unchecked") \
                                .classes("text-xl text-gray-300")
                            lbl = ui.label(display_name) \
                                .classes("text-[10px] text-gray-400 text-center")
                            stage_icons[node_name] = icon
                            stage_labels[node_name] = lbl
                        if i < len(PIPELINE_STAGES) - 1:
                            ui.icon("chevron_right").classes("text-gray-300 text-xs")

                status_label = ui.label("Starting...").classes("text-sm text-blue-600 mt-2")
                duration_label = ui.label("").classes("text-xs text-gray-400")

            # Process this claim in background thread
            start_time = datetime.now()
            result_holder: dict = {"result": None, "error": None}

            def _process():
                try:
                    from rcm_denial.workflows.denial_graph import process_claim
                    result = process_claim(
                        claim.model_dump(),
                        batch_id=state.batch_id,
                    )
                    result_holder["result"] = result
                except Exception as exc:
                    result_holder["error"] = str(exc)

            # Run in executor and poll for stage updates
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(None, _process)

            # Poll audit log for progress while pipeline runs
            poll_count = 0
            while not future.done():
                await asyncio.sleep(0.8)
                poll_count += 1
                elapsed = (datetime.now() - start_time).total_seconds()
                duration_label.set_text(f"Elapsed: {elapsed:.1f}s")

                # Query audit log for this claim's progress
                try:
                    _update_stage_indicators(
                        claim_id, state.batch_id,
                        stage_icons, stage_labels, status_label,
                    )
                except Exception:
                    pass

            await future  # ensure done
            elapsed = (datetime.now() - start_time).total_seconds()

            # Mark all completed stages
            try:
                _update_stage_indicators(
                    claim_id, state.batch_id,
                    stage_icons, stage_labels, status_label,
                )
            except Exception:
                pass

            # Build completed entry
            result = result_holder["result"]
            error = result_holder["error"]

            if error:
                completed_info = {
                    **claim_info,
                    "status": "failed",
                    "package_type": "failed",
                    "duration_s": round(elapsed, 1),
                    "error": error,
                }
            elif result:
                # process_claim returns SubmissionPackage directly
                pkg = result
                completed_info = {
                    **claim_info,
                    "status": getattr(pkg, "status", "unknown"),
                    "package_type": getattr(pkg, "package_type", "unknown"),
                    "run_id": getattr(pkg, "run_id", ""),
                    "batch_id": state.batch_id,
                    "duration_s": round(elapsed, 1),
                    "output_dir": getattr(pkg, "output_dir", ""),
                    "routing": getattr(pkg, "package_type", ""),
                }
            else:
                completed_info = {
                    **claim_info,
                    "status": "unknown",
                    "package_type": "unknown",
                    "duration_s": round(elapsed, 1),
                }

            state.completed.append(completed_info)

            # Update completed panel
            completed_container.clear()
            with completed_container:
                for c in state.completed:
                    _completed_claim_card(c)
            completed_count.set_text(f"{len(state.completed)} claims")

            # Check cancel before processing next claim
            if state.cancel_requested:
                remaining_count = total - idx - 1
                center_container.clear()
                with center_container:
                    ui.icon("cancel").classes("text-5xl text-orange-500")
                    ui.label("Processing cancelled").classes("text-xl font-bold text-orange-700")
                    ui.label(
                        f"{len(state.completed)} claims processed, "
                        f"{remaining_count} skipped"
                    ).classes("text-gray-500")
                    with ui.row().classes("gap-4 mt-4"):
                        ui.button("View Stats", icon="bar_chart",
                                  on_click=lambda: ui.navigate.to("/stats")).props("color=primary")
                        ui.button("Review Queue", icon="rate_review",
                                  on_click=lambda: ui.navigate.to("/review")).props("flat")
                ui.notify(f"Cancelled: {len(state.completed)} processed, {remaining_count} skipped", type="warning")
                break

        else:
            # Loop completed without break (no cancel)
            center_container.clear()
            with center_container:
                ui.icon("check_circle").classes("text-5xl text-green-600")
                ui.label(f"Batch {state.batch_id} complete!").classes("text-xl font-bold text-green-700")
                ui.label(f"{len(state.completed)} claims processed").classes("text-gray-500")
                with ui.row().classes("gap-4 mt-4"):
                    ui.button("View Stats", icon="bar_chart",
                              on_click=lambda: ui.navigate.to("/stats")).props("color=primary")
                    ui.button("Review Queue", icon="rate_review",
                              on_click=lambda: ui.navigate.to("/review")).props("flat")

            ui.notify(f"Batch complete: {len(state.completed)} claims", type="positive")

            # Push metrics to Prometheus Pushgateway (for Grafana)
            try:
                from rcm_denial.config.settings import settings as _settings
                if _settings.metrics_export_after_batch:
                    from rcm_denial.services.metrics_service import collect_and_export, push_to_gateway
                    collect_and_export(batch_id=state.batch_id)
                    if _settings.prometheus_pushgateway_url:
                        push_to_gateway(_settings.prometheus_pushgateway_url, batch_id=state.batch_id)
            except Exception:
                pass  # non-fatal

    except Exception as exc:
        center_container.clear()
        with center_container:
            ui.label(f"Batch error: {exc}").classes("text-red-600")
            ui.code(traceback.format_exc()).classes("text-xs w-full")
        ui.notify(f"Error: {exc}", type="negative")

    finally:
        state.is_running = False
        state.cancel_requested = False
        state.current = None
        process_btn.props(remove="disable loading")
        cancel_btn.set_visibility(False)


# ──────────────────────────────────────────────────────────────────────
# Poll audit log to update pipeline stage indicators
# ──────────────────────────────────────────────────────────────────────

def _update_stage_indicators(
    claim_id: str,
    batch_id: str,
    stage_icons: dict[str, ui.icon],
    stage_labels: dict[str, ui.label],
    status_label: ui.label,
) -> None:
    """
    Query checkpoint table for completed nodes and update UI.
    Checkpoints are written per-node during processing (unlike audit_log
    which is written after the claim fully completes).
    """
    from rcm_denial.config.settings import settings
    db_path = settings.data_dir / "rcm_denial.db"
    if not db_path.exists():
        return

    # Strategy 1: Check checkpoint table (written per-node in real-time)
    checkpoint_node = None
    checkpoint_index = -1
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT last_completed_node, node_index FROM claim_checkpoint
                WHERE claim_id = ? AND batch_id = ?
                """,
                (claim_id, batch_id),
            ).fetchone()
            if row:
                checkpoint_node = row[0]
                checkpoint_index = row[1]
    except Exception:
        pass

    # Strategy 2: Fallback to audit_log (written after claim completes)
    audit_nodes: dict[str, str] = {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT node_name, status FROM claim_audit_log
                WHERE claim_id = ? AND batch_id = ?
                ORDER BY id ASC
                """,
                (claim_id, batch_id),
            ).fetchall()
            for node_name, node_status in rows:
                audit_nodes[node_name] = node_status
    except Exception:
        pass

    # Merge: checkpoint tells us which nodes completed; audit gives detail
    from rcm_denial.services.checkpoint_service import NODE_ORDER

    completed_nodes: set[str] = set()
    current_node: str | None = None

    if checkpoint_index >= 0:
        # All nodes up to checkpoint_index are completed
        for i, node_name in enumerate(NODE_ORDER):
            if i <= checkpoint_index:
                completed_nodes.add(node_name)
            elif i == checkpoint_index + 1:
                current_node = node_name  # next node is likely running now
                break

    # Also incorporate audit_log entries
    for node_name, node_status in audit_nodes.items():
        if node_status == "completed":
            completed_nodes.add(node_name)
        elif node_status == "started" and node_name not in completed_nodes:
            current_node = node_name
        elif node_status == "failed":
            if node_name in stage_icons:
                stage_icons[node_name]._props["name"] = "error"
                stage_icons[node_name].classes(replace="text-xl text-red-500")
                stage_labels[node_name].classes(replace="text-[10px] text-red-500")
                stage_icons[node_name].update()
                stage_labels[node_name].update()

    # Update completed nodes
    for node_name in completed_nodes:
        if node_name in stage_icons:
            stage_icons[node_name]._props["name"] = "check_circle"
            stage_icons[node_name].classes(replace="text-xl text-green-600")
            stage_labels[node_name].classes(replace="text-[10px] text-green-600")
            stage_icons[node_name].update()
            stage_labels[node_name].update()

    # Update current running node
    if current_node and current_node in stage_icons and current_node not in completed_nodes:
        stage_icons[current_node]._props["name"] = "sync"
        stage_icons[current_node].classes(replace="text-xl text-blue-500 animate-spin")
        stage_labels[current_node].classes(replace="text-[10px] text-blue-600 font-bold")
        stage_icons[current_node].update()
        stage_labels[current_node].update()
        status_label.set_text(f"Running: {current_node}")
        status_label.update()
    elif completed_nodes:
        last = max(completed_nodes, key=lambda n: NODE_ORDER.index(n) if n in NODE_ORDER else -1)
        status_label.set_text(f"Completed: {last}")
        status_label.update()


# ──────────────────────────────────────────────────────────────────────
# Completed claim card
# ──────────────────────────────────────────────────────────────────────

def _completed_claim_card(info: dict) -> None:
    status = info.get("status", "unknown")
    color = "green" if status == "complete" else ("orange" if status == "partial" else "red")
    claim_id = info.get("claim_id", "?")
    payer = info.get("payer_id", "?")
    pkg = info.get("package_type", "?")
    duration = info.get("duration_s", 0)
    run_id = info.get("run_id", "")
    batch_id = info.get("batch_id", "")
    error = info.get("error", "")

    with ui.card().classes(f"w-full px-2 py-1 bg-{color}-50 border-l-4 border-{color}-500"):
        with ui.row().classes("items-center justify-between"):
            with ui.column().classes("gap-0"):
                if run_id:
                    ui.link(claim_id, f"/claim/{run_id}") \
                        .classes("text-xs font-semibold text-blue-700 underline")
                else:
                    ui.label(claim_id).classes("text-xs font-semibold")
                ui.label(f"{payer} | {pkg} | {duration}s").classes("text-[10px] text-gray-500")
                if batch_id:
                    ui.label(f"Batch: {batch_id}").classes("text-[9px] text-gray-400")

            ui.badge(status[:6].upper(), color=color).props("dense")

        if error:
            ui.label(f"{error[:80]}").classes("text-[10px] text-red-500")

        if run_id:
            with ui.row().classes("gap-1"):
                ui.button("Review", icon="rate_review",
                          on_click=lambda r=run_id: ui.navigate.to(f"/claim/{r}")) \
                    .props("flat dense round size=xs color=primary") \
                    .tooltip("View claim detail")
                output_dir = info.get("output_dir", "")
                if output_dir:
                    claim_dir = Path(output_dir).name
                    ui.button("PDF", icon="picture_as_pdf",
                              on_click=lambda d=claim_dir: ui.navigate.to(f"/output/{d}/")) \
                        .props("flat dense round size=xs color=grey") \
                        .tooltip("Download PDF package")


# ──────────────────────────────────────────────────────────────────────
# SOP Init from web
# ──────────────────────────────────────────────────────────────────────

async def _run_sop_init(center_container):
    center_container.clear()
    with center_container:
        spinner = ui.spinner("dots", size="lg")
        status_label = ui.label("Checking SOP collections...").classes("text-blue-600 text-sm")

    try:
        from rcm_denial.services.sop_ingestion import ingest_all_payer_sops

        # This will skip already-fresh collections automatically
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ingest_all_payer_sops(run_verify=True)
        )
        spinner.set_visibility(False)

        # Show results from manifest
        from rcm_denial.services.sop_ingestion import read_manifest
        manifest = read_manifest()
        payers = manifest.get("payers", {})

        with center_container:
            center_container.clear()
            ui.icon("check_circle").classes("text-3xl text-green-600")
            ui.label("SOP collections ready").classes("text-base font-semibold text-green-700")

            if payers:
                cols = [
                    {"name": "payer", "label": "Payer", "field": "payer"},
                    {"name": "docs", "label": "Docs", "field": "docs"},
                    {"name": "status", "label": "Status", "field": "status"},
                    {"name": "note", "label": "Note", "field": "note"},
                ]
                rows = []
                for key, info in sorted(payers.items()):
                    built_now = result.get(key, 0)
                    was_skipped = info.get("status") == "ok" and built_now == info.get("document_count", 0)
                    rows.append({
                        "payer": key,
                        "docs": info.get("document_count", 0),
                        "status": info.get("status", "?").upper(),
                        "note": "Already up-to-date" if was_skipped else "Built/refreshed",
                    })
                ui.table(columns=cols, rows=rows, row_key="payer") \
                    .props("dense flat").classes("w-full mt-2")

        ui.notify("SOP collections ready", type="positive")

    except Exception as exc:
        spinner.set_visibility(False)
        with center_container:
            center_container.clear()
            ui.label(f"SOP init failed: {exc}").classes("text-red-600")
        ui.notify(f"Error: {exc}", type="negative")
