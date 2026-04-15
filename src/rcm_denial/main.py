##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: main.py
# Purpose: CLI entry point for the RCM Denial Management system.
#          Supports three commands: process-batch, process-claim,
#          and seed-kb. Exposes the public API functions for
#          integration into existing applications.
#
##########################################################

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

console = Console()


# ------------------------------------------------------------------ #
# CLI group
# ------------------------------------------------------------------ #

@click.group()
@click.version_option(version="1.0.0", prog_name="rcm-denial")
def cli():
    """
    RCM Denial Management — Agentic AI system for processing
    denied medical claims using LangGraph multi-agent workflows.
    """
    pass


# ------------------------------------------------------------------ #
# Command: process-batch
# ------------------------------------------------------------------ #

@cli.command("process-batch")
@click.argument("csv_path", type=click.Path(exists=True))
@click.option("--batch-id", default="", help="Optional batch identifier")
@click.option("--no-skip", is_flag=True, default=False, help="Re-process already completed claims")
@click.option(
    "--source", default="claim_db",
    help="Field mapping key for the CSV source system (default: claim_db). "
         "Add new sources to FIELD_MAPS in claim_intake.py.",
)
@click.option(
    "--on-error", "on_error",
    type=click.Choice(["proceed", "stop"], case_sensitive=False),
    default="proceed",
    help="proceed (default): log bad rows, continue. "
         "stop: halt on first validation error — useful when testing a new integration.",
)
def process_batch_cmd(csv_path: str, batch_id: str, no_skip: bool, source: str, on_error: str):
    """
    Process all denied claims in a CSV file.

    CSV_PATH: Path to the input CSV file containing denied claims.

    Examples:\n
        rcm-denial process-batch data/claims.csv\n
        rcm-denial process-batch data/claims.csv --source epic --on-error stop
    """
    from rcm_denial.workflows.batch_processor import process_batch

    console.print(Panel.fit(
        f"[bold blue]RCM Denial Management[/bold blue]\n"
        f"Processing batch : [cyan]{csv_path}[/cyan]\n"
        f"Source mapping   : [yellow]{source}[/yellow]   "
        f"On-error         : [yellow]{on_error}[/yellow]",
        border_style="blue",
    ))

    try:
        report = process_batch(
            csv_path=csv_path,
            batch_id=batch_id,
            skip_completed=not no_skip,
            source=source,
            on_error=on_error,
        )

        # Summary table
        table = Table(title="Batch Processing Results", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        table.add_row("Batch ID", report.batch_id)
        table.add_row("Input CSV", report.input_csv)
        table.add_row("Source Mapping", report.source_mapping)
        table.add_row("Total Rows", str(report.total_claims))
        table.add_row("Rejected (validation)", f"[red]{report.rejected}[/red]")
        table.add_row("Completed", f"[green]{report.completed}[/green]")
        table.add_row("Partial", f"[yellow]{report.partial}[/yellow]")
        table.add_row("Failed", f"[red]{report.failed}[/red]")
        table.add_row("Skipped", str(report.skipped))
        table.add_row("Success Rate", f"{report.success_rate}%")
        table.add_row(
            "Duration",
            f"{report.total_duration_ms / 1000:.1f}s" if report.total_duration_ms else "N/A",
        )

        console.print(table)

        # Per-claim results
        if report.claim_results:
            detail_table = Table(title="Per-Claim Results", show_header=True)
            detail_table.add_column("Claim ID", style="cyan")
            detail_table.add_column("Status")
            detail_table.add_column("Package Type")
            detail_table.add_column("Duration (s)")
            detail_table.add_column("Errors")

            for r in report.claim_results:
                status_color = {"complete": "green", "partial": "yellow", "failed": "red", "skipped": "grey50"}.get(r.status, "white")
                detail_table.add_row(
                    r.claim_id,
                    f"[{status_color}]{r.status}[/{status_color}]",
                    r.package_type or "-",
                    f"{r.duration_ms / 1000:.1f}" if r.duration_ms else "-",
                    str(len(r.errors)),
                )

            console.print(detail_table)

        console.print(f"\n[green]✓[/green] Batch summary written to output/batch_summary_{report.batch_id}.json")

    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]UNEXPECTED ERROR:[/red] {exc}")
        raise


# ------------------------------------------------------------------ #
# Command: process-claim
# ------------------------------------------------------------------ #

@cli.command("process-claim")
@click.option("--claim-id", required=True, help="Claim ID")
@click.option("--patient-id", required=True, help="Patient ID")
@click.option("--payer-id", required=True, help="Payer ID")
@click.option("--provider-id", required=True, help="Provider ID")
@click.option("--dos", required=True, help="Date of service (YYYY-MM-DD)")
@click.option("--cpt", required=True, help="CPT codes (comma-separated)")
@click.option("--dx", required=True, help="Diagnosis codes (comma-separated)")
@click.option("--denial-reason", required=True, help="Denial reason text")
@click.option("--carc", required=True, help="CARC code")
@click.option("--rarc", default="", help="RARC code (optional)")
@click.option("--denial-date", required=True, help="Denial date (YYYY-MM-DD)")
@click.option("--amount", required=True, type=float, help="Billed amount (USD)")
@click.option("--eob-path", default="", help="Path to EOB PDF (optional)")
def process_claim_cmd(**kwargs):
    """
    Process a single denied claim from command-line arguments.

    Example:
        rcm-denial process-claim --claim-id CLM-001 --patient-id PAT001 \\
            --payer-id BCBS --provider-id PROV-001 --dos 2024-09-20 \\
            --cpt 27447 --dx M17.11 --denial-reason "Prior auth missing" \\
            --carc 97 --denial-date 2024-10-05 --amount 15000
    """
    from rcm_denial.workflows.denial_graph import process_claim

    claim_data = {
        "claim_id": kwargs["claim_id"],
        "patient_id": kwargs["patient_id"],
        "payer_id": kwargs["payer_id"],
        "provider_id": kwargs["provider_id"],
        "date_of_service": kwargs["dos"],
        "cpt_codes": kwargs["cpt"],
        "diagnosis_codes": kwargs["dx"],
        "denial_reason": kwargs["denial_reason"],
        "carc_code": kwargs["carc"],
        "rarc_code": kwargs.get("rarc") or None,
        "denial_date": kwargs["denial_date"],
        "billed_amount": kwargs["amount"],
        "eob_pdf_path": kwargs.get("eob_path") or None,
    }

    console.print(Panel.fit(
        f"[bold blue]Processing Claim:[/bold blue] [cyan]{kwargs['claim_id']}[/cyan]\n"
        f"Payer: {kwargs['payer_id']}  |  CARC: {kwargs['carc']}  |  Amount: ${kwargs['amount']:,.2f}",
        border_style="blue",
    ))

    try:
        package = process_claim(claim_data)

        status_color = {"complete": "green", "partial": "yellow", "failed": "red"}.get(package.status, "white")
        console.print(f"\n[{status_color}]Status:[/{status_color}] {package.status.upper()}")
        console.print(f"[cyan]Package type:[/cyan] {package.package_type}")
        console.print(f"[cyan]Output directory:[/cyan] {package.output_dir}")
        if package.pdf_package_path:
            console.print(f"[cyan]PDF package:[/cyan] {package.pdf_package_path}")
        console.print(f"\n[italic]{package.summary}[/italic]")

    except Exception as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise


# ------------------------------------------------------------------ #
# Command: intake-report
# ------------------------------------------------------------------ #

@cli.command("intake-report")
@click.option("--batch-id", default="", help="Filter by batch ID")
@click.option("--csv", "csv_path", default="", help="Filter by source CSV path")
def intake_report_cmd(batch_id: str, csv_path: str):
    """
    Show intake validation results from the database.

    Displays counts of valid vs rejected rows and details on every
    rejected row so the user knows exactly what to fix in the CSV.

    Examples:\n
        rcm-denial intake-report --batch-id abc123\n
        rcm-denial intake-report --csv data/claims.csv
    """
    from rcm_denial.services.claim_intake import get_intake_report

    report = get_intake_report(batch_id=batch_id, source_file=csv_path)

    console.print(Panel.fit(
        f"[bold blue]Intake Validation Report[/bold blue]",
        border_style="blue",
    ))

    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column("Key", style="cyan")
    summary.add_column("Value")
    if batch_id:
        summary.add_row("Batch ID",    batch_id)
    if csv_path:
        summary.add_row("Source File", csv_path)
    summary.add_row("Total Rows",  str(report["total"]))
    summary.add_row("Valid",       f"[green]{report['valid']}[/green]")
    summary.add_row("Rejected",    f"[red]{report['rejected']}[/red]")
    console.print(summary)

    if report["rejected_details"]:
        console.print()
        rej_table = Table(
            title="Rejected Rows — Fix These in the CSV",
            show_header=True,
            header_style="bold red",
        )
        rej_table.add_column("Row", style="cyan", no_wrap=True)
        rej_table.add_column("Claim ID", style="cyan")
        rej_table.add_column("Errors")
        rej_table.add_column("Recorded At", style="dim")

        for r in report["rejected_details"]:
            errors_text = "\n".join(r["errors"])
            rej_table.add_row(
                str(r["row_number"]),
                r["claim_id"] or "-",
                errors_text,
                str(r["recorded_at"]),
            )
        console.print(rej_table)
    else:
        console.print("\n[green]✓[/green] No rejected rows.")


# ------------------------------------------------------------------ #
# Command: ingest-sop
# ------------------------------------------------------------------ #

@cli.command("ingest-sop")
@click.option(
    "--payer", "payer_id", default="",
    help="Payer ID to ingest SOPs for (e.g. BCBS, Aetna, Medicare). "
         "Omit to ingest ALL payer subdirectories under data/sop_documents/.",
)
@click.option(
    "--dir", "documents_dir", default="",
    help="Custom SOP documents directory. "
         "Defaults to data/sop_documents/{payer_id}/.",
)
@click.option("--verify", is_flag=True, default=False,
              help="Run a test query after indexing to confirm the collection works")
def ingest_sop_cmd(payer_id: str, documents_dir: str, verify: bool):
    """
    Index SOP documents into per-payer ChromaDB collections.

    SOP directory layout expected:
        data/sop_documents/
          global/    <- generic SOPs for all payers
          bcbs/      <- BCBS-specific SOPs
          aetna/     <- Aetna-specific SOPs
          ...

    Supported file types: .txt .md .pdf .json

    Requires OPENAI_API_KEY to be set in .env.

    Examples:\n
        rcm-denial ingest-sop                         # index all payers\n
        rcm-denial ingest-sop --payer BCBS            # index BCBS only\n
        rcm-denial ingest-sop --payer BCBS --verify   # index + test query\n
        rcm-denial ingest-sop --payer BCBS --dir /path/to/bcbs_sops
    """
    from rcm_denial.services.sop_ingestion import ingest_all_payer_sops, ingest_sop_documents

    if payer_id:
        console.print(Panel.fit(
            f"[bold blue]SOP Ingestion[/bold blue]\n"
            f"Payer: [cyan]{payer_id}[/cyan]"
            + (f"\nDir: [cyan]{documents_dir}[/cyan]" if documents_dir else ""),
            border_style="blue",
        ))
        count = ingest_sop_documents(payer_id, documents_dir=documents_dir or None, run_verify=verify)
        if count > 0:
            console.print(f"[green]✓[/green] Indexed [bold]{count}[/bold] documents for payer [cyan]{payer_id}[/cyan]")
        else:
            console.print(f"[yellow]⚠[/yellow] No documents indexed for [cyan]{payer_id}[/cyan] — check logs")
            sys.exit(1)
    else:
        console.print(Panel.fit(
            "[bold blue]SOP Ingestion — All Payers[/bold blue]",
            border_style="blue",
        ))
        results = ingest_all_payer_sops(run_verify=verify)

        table = Table(title="SOP Ingestion Results", show_header=True, header_style="bold magenta")
        table.add_column("Payer Key", style="cyan")
        table.add_column("Collection", style="dim")
        table.add_column("Documents Indexed", style="green")

        total = 0
        for pkey, count in sorted(results.items()):
            table.add_row(pkey, f"sop_{pkey}", str(count))
            total += count

        console.print(table)
        console.print(f"\n[green]✓[/green] Total: [bold]{total}[/bold] documents across [bold]{len(results)}[/bold] collections")


# ------------------------------------------------------------------ #
# Command: sop-status
# ------------------------------------------------------------------ #

@cli.command("sop-status")
def sop_status_cmd():
    """
    Show status of all per-payer SOP ChromaDB collections.

    Displays collection name, document count, last indexed timestamp,
    and whether the SOP document directory exists.

    Examples:\n
        rcm-denial sop-status
    """
    from rcm_denial.services.sop_ingestion import get_collection_stats

    stats = get_collection_stats()

    console.print(Panel.fit(
        "[bold blue]SOP Collection Status[/bold blue]",
        border_style="blue",
    ))

    if not stats:
        console.print("[yellow]No SOP collections found.[/yellow]")
        console.print("Run [cyan]rcm-denial ingest-sop[/cyan] to create them.")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Payer Key",    style="cyan")
    table.add_column("Collection",   style="dim")
    table.add_column("Documents",    style="green")
    table.add_column("Last Indexed", style="dim")
    table.add_column("SOP Dir",      style="dim")

    for s in stats:
        dir_status = "[green]exists[/green]" if s["sop_dir_exists"] else "[red]missing[/red]"
        table.add_row(
            s["payer_key"],
            s["collection_name"],
            str(s["document_count"]),
            s["indexed_at"],
            dir_status,
        )

    console.print(table)
    console.print(f"\n[dim]ChromaDB path: data/chroma_db/[/dim]")


# ------------------------------------------------------------------ #
# Command: init  (pre-flight SOP RAG setup)
# ------------------------------------------------------------------ #

@cli.command("init")
@click.option(
    "--payer", "payer_id", default="",
    help="Init a specific payer only (e.g. BCBS). Omit to init ALL payers.",
)
@click.option("--check-only", is_flag=True, default=False,
              help="Report manifest status without rebuilding any collections")
@click.option("--strict", is_flag=True, default=False,
              help="Exit non-zero if any payer is missing or degraded after init")
@click.option("--verify", is_flag=True, default=False,
              help="Run a test query per payer after indexing (slower but confirms queries work)")
def init_cmd(payer_id: str, check_only: bool, strict: bool, verify: bool):
    """
    Pre-flight SOP RAG setup — run this before starting any batch.

    Discovers all payer folders in data/sop_documents/, builds (or
    refreshes) their ChromaDB collections, and writes a manifest file.
    The main pipeline never triggers indexing — it only queries.

    SOP folder layout:
        data/sop_documents/
          global/       <- fallback SOPs for all payers
          bcbs/         <- BCBS-specific
          aetna/
          medicare/
          ...

    Examples:\n
        rcm-denial init                    # build all payer collections\n
        rcm-denial init --payer BCBS       # build BCBS only\n
        rcm-denial init --check-only       # report status, no rebuilding\n
        rcm-denial init --verify           # build + test-query each payer\n
        rcm-denial init --strict           # fail if any collection missing
    """
    from rcm_denial.services.sop_ingestion import (
        check_payer_coverage,
        get_collection_stats,
        ingest_all_payer_sops,
        ingest_sop_documents,
        read_manifest,
        verify_collection_query,
    )
    from rcm_denial.config.settings import settings

    sop_root = settings.sop_documents_dir
    sop_root.mkdir(parents=True, exist_ok=True)

    # ── Discover payer folders ────────────────────────────────────────
    payer_dirs = [
        d for d in sorted(sop_root.iterdir())
        if d.is_dir() and d.name != "manifest.json"
    ]
    if not payer_dirs:
        console.print(
            f"[yellow]No payer subdirectories found under {sop_root}.[/yellow]\n"
            "Create folders per payer before running init:\n"
            "  mkdir -p data/sop_documents/bcbs\n"
            "  mkdir -p data/sop_documents/aetna\n"
            "  # ... place SOP documents (.pdf, .txt, .md, .json) inside"
        )
        sys.exit(1)

    title = "SOP Pre-Flight — Check Only" if check_only else "SOP Pre-Flight Initialization"
    payer_label = f"Payer: [cyan]{payer_id}[/cyan]" if payer_id else f"Payers found: [bold]{len(payer_dirs)}[/bold]"
    console.print(Panel.fit(
        f"[bold blue]{title}[/bold blue]\n"
        f"{payer_label}   SOP root: [dim]{sop_root}[/dim]",
        border_style="blue",
    ))

    # ── Check-only mode: just read manifest and report ────────────────
    if check_only:
        stats = get_collection_stats()
        manifest = read_manifest()

        table = Table(title="SOP Collection Status", show_header=True, header_style="bold magenta")
        table.add_column("Payer",        style="cyan")
        table.add_column("Folder",       style="dim")
        table.add_column("Status",       no_wrap=True)
        table.add_column("Docs",         justify="right")
        table.add_column("Indexed At",   style="dim")
        table.add_column("Verify Hits",  justify="right")

        status_map = {s["payer_key"]: s for s in stats}
        all_dirs = [d.name for d in payer_dirs]

        ok_count = 0
        for pkey in all_dirs:
            s = status_map.get(pkey)
            status = s["status"] if s else "missing"
            color = {"ok": "green", "empty": "yellow", "missing": "red", "stale": "yellow", "error": "red"}.get(status, "white")
            if status == "ok":
                ok_count += 1
            table.add_row(
                pkey,
                "✓" if (s and s["sop_dir_exists"]) else "[red]✗[/red]",
                f"[{color}]{status}[/{color}]",
                str(s["document_count"]) if s else "-",
                str(s.get("indexed_at") or "-")[:19] if s else "-",
                str(s.get("verify_hit_count") or "-") if s else "-",
            )

        console.print(table)
        manifest_updated = manifest.get("last_updated", "never")
        console.print(f"\n[dim]Manifest last updated: {manifest_updated}[/dim]")

        if strict and ok_count < len(all_dirs):
            console.print(f"\n[red]✗[/red] {len(all_dirs) - ok_count} collection(s) not ready.")
            sys.exit(1)
        else:
            console.print(f"\n[green]✓[/green] {ok_count}/{len(all_dirs)} collection(s) ready.")
        return

    # ── Build / refresh collections ───────────────────────────────────
    if payer_id:
        payers_to_init = [payer_id]
        results = {payer_id: ingest_sop_documents(payer_id, run_verify=verify)}
    else:
        results = ingest_all_payer_sops(run_verify=verify)
        payers_to_init = list(results.keys())

    # ── Results table ─────────────────────────────────────────────────
    table = Table(title="Initialization Results", show_header=True, header_style="bold magenta")
    table.add_column("Payer",     style="cyan")
    table.add_column("Docs",      justify="right", style="bold")
    table.add_column("Status",    no_wrap=True)
    table.add_column("Test Query",justify="right", style="dim")

    ok_count  = 0
    fail_list = []
    manifest  = read_manifest()
    payer_entries = manifest.get("payers", {})

    for pkey in sorted(results.keys()):
        count  = results[pkey]
        entry  = payer_entries.get(pkey, {})
        status = entry.get("status", "ok" if count > 0 else "empty")
        color  = "green" if status == "ok" else "red"
        hits   = str(entry.get("verify_hit_count") or "-") if verify else "-"

        if status == "ok":
            ok_count += 1
        else:
            fail_list.append(pkey)

        table.add_row(
            pkey,
            str(count),
            f"[{color}]{status}[/{color}]",
            hits,
        )

    console.print(table)

    total_docs = sum(results.values())
    console.print(
        f"\n[green]✓[/green] Initialized [bold]{ok_count}[/bold] payer(s), "
        f"[bold]{total_docs}[/bold] total documents."
    )
    console.print(f"[dim]Manifest written to: {sop_root / 'manifest.json'}[/dim]")

    if fail_list:
        console.print(
            f"[yellow]⚠[/yellow] {len(fail_list)} payer(s) could not be initialized: "
            f"{', '.join(fail_list)}\n"
            "These payers will fall back to keyword search during the pipeline."
        )
        if strict:
            sys.exit(1)
    else:
        console.print(
            "[green]✓[/green] All collections ready. "
            "You can now run [cyan]rcm-denial process-batch[/cyan]."
        )


# ------------------------------------------------------------------ #
# Command group: review-queue
# ------------------------------------------------------------------ #

@cli.group("review-queue")
def review_queue_grp():
    """
    Human review queue — approve, re-route, override, or write-off claims.

    All claims land here after pipeline processing. The reviewer works
    through the queue independently of batch execution.

    Examples:\n
        rcm-denial review-queue list\n
        rcm-denial review-queue list --batch-id abc123 --status pending\n
        rcm-denial review-queue show --run-id <run_id>\n
        rcm-denial review-queue approve --run-id <run_id>\n
        rcm-denial review-queue approve-all --batch-id abc123\n
        rcm-denial review-queue re-route --run-id <run_id> --stage response_agent --notes "cite LCD L35023"\n
        rcm-denial review-queue override --run-id <run_id> --response-file letter.txt\n
        rcm-denial review-queue write-off --run-id <run_id> --reason timely_filing_expired\n
        rcm-denial review-queue stats --batch-id abc123
    """
    pass


@review_queue_grp.command("list")
@click.option("--batch-id", default="", help="Filter by batch ID")
@click.option(
    "--status", default="",
    type=click.Choice(
        ["", "pending", "approved", "re_routed", "re_processed",
         "human_override", "written_off", "submitted"],
        case_sensitive=False,
    ),
    help="Filter by status (default: all)",
)
@click.option("--limit", default=50, help="Max rows to display")
def review_list_cmd(batch_id: str, status: str, limit: int):
    """List claims in the review queue."""
    from rcm_denial.services.review_queue import get_queue

    items = get_queue(batch_id=batch_id, status=status, limit=limit)

    if not items:
        console.print("[yellow]No claims found in review queue.[/yellow]")
        return

    table = Table(
        title=f"Review Queue{' — ' + batch_id if batch_id else ''}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Claim ID",     style="cyan", no_wrap=True)
    table.add_column("Run ID",       style="dim",  no_wrap=True)
    table.add_column("Status",       no_wrap=True)
    table.add_column("Package",      style="dim")
    table.add_column("Amount",       justify="right")
    table.add_column("CARC",         style="yellow")
    table.add_column("Confidence",   justify="right")
    table.add_column("Reviews",      justify="right")
    table.add_column("Urgent",       justify="center")

    STATUS_COLORS = {
        "pending":        "yellow",
        "approved":       "green",
        "re_routed":      "blue",
        "re_processed":   "cyan",
        "human_override": "magenta",
        "written_off":    "red",
        "submitted":      "green",
    }

    for item in items:
        sc = STATUS_COLORS.get(item["status"], "white")
        urgent_mark = "[red]YES[/red]" if item.get("is_urgent") else "-"
        conf = item.get("confidence_score")
        conf_str = f"{conf:.0%}" if conf is not None else "-"
        table.add_row(
            item["claim_id"],
            item["run_id"][:12] + "...",
            f"[{sc}]{item['status']}[/{sc}]",
            item.get("package_type") or "-",
            f"${item['billed_amount']:,.0f}" if item.get("billed_amount") else "-",
            item.get("carc_code") or "-",
            conf_str,
            str(item.get("review_count") or 0),
            urgent_mark,
        )

    console.print(table)
    console.print(f"\n[dim]{len(items)} claim(s) shown[/dim]")


@review_queue_grp.command("show")
@click.option("--run-id", required=True, help="Run ID of the claim")
def review_show_cmd(run_id: str):
    """Show full detail for a single claim including AI summary."""
    from rcm_denial.services.review_queue import get_queue_item

    item = get_queue_item(run_id)
    if not item:
        console.print(f"[red]run_id {run_id!r} not found in review queue.[/red]")
        sys.exit(1)

    sc = {"approved": "green", "written_off": "red", "pending": "yellow"}.get(
        item["status"], "cyan"
    )
    console.print(Panel.fit(
        f"[bold]Claim:[/bold] [cyan]{item['claim_id']}[/cyan]   "
        f"[bold]Status:[/bold] [{sc}]{item['status']}[/{sc}]\n"
        f"[bold]Run ID:[/bold] {item['run_id']}   "
        f"[bold]Batch:[/bold] {item.get('batch_id') or '-'}",
        border_style="blue",
    ))

    if item.get("ai_summary"):
        console.print("\n[bold magenta]AI Summary:[/bold magenta]")
        console.print(item["ai_summary"])

    if item.get("reviewer_notes"):
        console.print(f"\n[bold]Reviewer notes:[/bold] {item['reviewer_notes']}")
    if item.get("reentry_node"):
        console.print(f"[bold]Re-entry stage:[/bold] {item['reentry_node']}")
    if item.get("write_off_reason"):
        console.print(f"[bold red]Write-off reason:[/bold red] {item['write_off_reason']}")
    if item.get("output_dir"):
        console.print(f"\n[dim]Output: {item['output_dir']}[/dim]")
    if item.get("pdf_package_path"):
        console.print(f"[dim]PDF: {item['pdf_package_path']}[/dim]")


@review_queue_grp.command("approve")
@click.option("--run-id", required=True)
@click.option("--reviewer", default="human", help="Reviewer identifier")
def review_approve_cmd(run_id: str, reviewer: str):
    """Approve a claim — triggers Phase 5 payer portal submission."""
    from rcm_denial.services.review_queue import approve

    item = approve(run_id=run_id, reviewer=reviewer)
    console.print(
        f"[green]✓[/green] Claim [cyan]{item.get('claim_id')}[/cyan] approved by {reviewer}. "
        "Ready for submission."
    )


@review_queue_grp.command("approve-all")
@click.option("--batch-id", default="", help="Limit to a specific batch")
@click.option("--confidence-above", default=0.85, type=float,
              help="Only approve if confidence >= this (default 0.85)")
@click.option("--amount-below", default=5000.0, type=float,
              help="Only approve if billed amount <= this (default $5,000)")
@click.option("--reviewer", default="human")
def review_approve_all_cmd(
    batch_id: str, confidence_above: float, amount_below: float, reviewer: str
):
    """Bulk-approve low-risk pending claims in one command."""
    from rcm_denial.services.review_queue import bulk_approve

    count = bulk_approve(
        batch_id=batch_id,
        confidence_above=confidence_above,
        amount_below=amount_below,
        reviewer=reviewer,
    )
    console.print(
        f"[green]✓[/green] Bulk approved [bold]{count}[/bold] claim(s) "
        f"(confidence >= {confidence_above:.0%}, amount <= ${amount_below:,.0f})."
    )


@review_queue_grp.command("re-route")
@click.option("--run-id", required=True)
@click.option(
    "--stage", required=True,
    type=click.Choice(
        ["intake_agent", "targeted_ehr_agent", "response_agent"],
        case_sensitive=False,
    ),
    help=(
        "intake_agent: re-process from scratch\n"
        "targeted_ehr_agent: fetch more clinical evidence\n"
        "response_agent: regenerate letter/plan with guidance"
    ),
)
@click.option("--notes", default="", help="Guidance injected into re-run LLM prompts")
@click.option("--reviewer", default="human")
@click.option("--execute", is_flag=True, default=False,
              help="Immediately run the re-entry pipeline (else just mark status)")
def review_reroute_cmd(
    run_id: str, stage: str, notes: str, reviewer: str, execute: bool
):
    """
    Re-route a claim back into the pipeline at a specific stage.

    Use --execute to immediately trigger the re-run, or run
    'rcm-denial review-queue execute --run-id <id>' separately.
    """
    from rcm_denial.services.review_queue import re_route as mark_reroute

    item = mark_reroute(run_id=run_id, stage=stage, notes=notes, reviewer=reviewer)
    console.print(
        f"[blue]↺[/blue] Claim [cyan]{item.get('claim_id')}[/cyan] "
        f"re-routed to [bold]{stage}[/bold]."
    )
    if notes:
        console.print(f"  Notes: [italic]{notes}[/italic]")

    if execute:
        _do_execute(run_id)


@review_queue_grp.command("execute")
@click.option("--run-id", required=True, help="Run ID of a re_routed or human_override claim")
def review_execute_cmd(run_id: str):
    """
    Execute a pending re-route or human-override action.

    Runs the pipeline from the chosen re-entry stage.
    The updated package lands back in the queue as 're_processed'.
    """
    _do_execute(run_id)


def _do_execute(run_id: str) -> None:
    """Shared execute logic for re-route and human-override."""
    from rcm_denial.services.review_queue import get_queue_item
    from rcm_denial.services.pipeline_reentry import re_route, apply_human_override

    item = get_queue_item(run_id)
    if not item:
        console.print(f"[red]run_id {run_id!r} not found.[/red]")
        sys.exit(1)

    status = item["status"]

    try:
        if status == "re_routed":
            console.print(
                f"[blue]↺[/blue] Re-running pipeline from "
                f"[bold]{item.get('reentry_node')}[/bold]..."
            )
            result = re_route(run_id)
            console.print(
                f"[green]✓[/green] Re-run complete. "
                f"Package: [cyan]{result.get('package_type', 'unknown')}[/cyan]. "
                "Claim is back in queue as 're_processed'."
            )

        elif status == "human_override":
            console.print("[magenta]✏[/magenta] Applying human override response...")
            result = apply_human_override(run_id)
            console.print(
                f"[green]✓[/green] Override applied. "
                f"Package: [cyan]{result.get('package_type', 'unknown')}[/cyan]. "
                "Claim is back in queue as 're_processed'."
            )

        else:
            console.print(
                f"[red]Cannot execute: claim status is '{status}'. "
                "Only 're_routed' or 'human_override' claims can be executed.[/red]"
            )
            sys.exit(1)

    except Exception as exc:
        console.print(f"[red]Execution failed:[/red] {exc}")
        raise


@review_queue_grp.command("override")
@click.option("--run-id", required=True)
@click.option(
    "--response-file", required=True,
    type=click.Path(exists=True),
    help="Path to a text file containing the human-written appeal response",
)
@click.option("--reviewer", default="human")
@click.option("--execute", is_flag=True, default=False,
              help="Immediately package and re-queue after override")
def review_override_cmd(run_id: str, response_file: str, reviewer: str, execute: bool):
    """
    Replace the AI-generated response with a human-written one.

    The response-file should contain the full appeal letter or
    correction notes written by the billing specialist.
    """
    from rcm_denial.services.review_queue import human_override

    response_text = Path(response_file).read_text(encoding="utf-8").strip()
    if not response_text:
        console.print(f"[red]Response file {response_file!r} is empty.[/red]")
        sys.exit(1)

    item = human_override(run_id=run_id, response_text=response_text, reviewer=reviewer)
    console.print(
        f"[magenta]✏[/magenta] Human override saved for claim "
        f"[cyan]{item.get('claim_id')}[/cyan] ({len(response_text)} chars)."
    )

    if execute:
        _do_execute(run_id)
    else:
        console.print(
            "Run [cyan]rcm-denial review-queue execute --run-id "
            f"{run_id}[/cyan] to package and re-queue."
        )


@review_queue_grp.command("write-off")
@click.option("--run-id", required=True)
@click.option(
    "--reason", required=True,
    type=click.Choice(
        ["timely_filing_expired", "cost_exceeds_recovery", "payer_non_negotiable",
         "duplicate_confirmed_paid", "patient_responsibility", "other"],
        case_sensitive=False,
    ),
)
@click.option("--notes", default="", help="Additional write-off justification notes")
@click.option("--reviewer", default="human")
@click.option("--force", is_flag=True, default=False,
              help="Manager override: bypass the re-route-first guard")
def review_writeoff_cmd(
    run_id: str, reason: str, notes: str, reviewer: str, force: bool
):
    """
    Mark a claim as written off (last resort — tracked as revenue loss).

    Blocked unless a re-route was attempted first OR reason is
    'timely_filing_expired'. Use --force for manager override.
    """
    from rcm_denial.services.review_queue import write_off
    from rcm_denial.services.pipeline_reentry import finalize_write_off

    try:
        item = write_off(
            run_id=run_id, reason=reason, notes=notes,
            reviewer=reviewer, force=force,
        )
    except PermissionError as exc:
        console.print(f"[red]Write-off blocked:[/red] {exc}")
        sys.exit(1)

    amount = item.get("billed_amount") or 0.0
    console.print(
        f"[red]✗[/red] Claim [cyan]{item.get('claim_id')}[/cyan] "
        f"written off. Reason: {reason}. "
        f"[bold red]Revenue impact: ${amount:,.2f}[/bold red]"
    )

    finalize_write_off(run_id)
    console.print("[dim]Write-off documentation generated.[/dim]")


@review_queue_grp.command("stats")
@click.option("--batch-id", default="", help="Filter by batch ID (default: all-time)")
def review_stats_cmd(batch_id: str):
    """
    Show HITL review metrics including write-off revenue impact.

    Key metric: write_off_count should trend toward 0.
    """
    from rcm_denial.services.review_queue import get_review_stats

    stats = get_review_stats(batch_id=batch_id)

    title = f"Review Queue Stats{' — Batch ' + batch_id if batch_id else ' — All Time'}"
    console.print(Panel.fit(f"[bold blue]{title}[/bold blue]", border_style="blue"))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value")

    table.add_row("Total claims",           str(stats.get("total_claims", 0)))
    table.add_row("Approved / Submitted",   f"[green]{stats.get('approved', 0)}[/green]")
    table.add_row("Pending review",         f"[yellow]{stats.get('pending', 0)}[/yellow]")
    table.add_row("Re-routed",              str(stats.get("re_routed", 0)))
    table.add_row("Re-processed",           str(stats.get("re_processed", 0)))
    table.add_row("Human override",         f"[magenta]{stats.get('human_override_count', 0)}[/magenta]")
    table.add_row("─" * 30, "─" * 10)
    table.add_row(
        "Write-offs",
        f"[bold red]{stats.get('write_off_count', 0)}[/bold red]",
    )
    table.add_row(
        "Write-off revenue impact",
        f"[bold red]{stats.get('write_off_revenue_impact', '$0.00')}[/bold red]",
    )
    table.add_row(
        "Preventable write-offs",
        f"[red]{stats.get('write_off_preventable', 0)}[/red]",
    )
    table.add_row("─" * 30, "─" * 10)
    table.add_row("Recovery rate",          f"{stats.get('recovery_rate_pct', 0)}%")
    table.add_row("Avg review cycles/claim",str(stats.get("avg_review_cycles", 0)))

    console.print(table)

    if stats.get("write_off_count", 0) > 0:
        console.print(
            "\n[bold red]⚑ Write-offs detected.[/bold red] "
            "Review preventable write-offs and consider re-routing before finalizing."
        )


# ------------------------------------------------------------------ #
# Command: submit
# ------------------------------------------------------------------ #

@cli.command("submit")
@click.option("--run-id", required=True, help="Run ID of the approved claim to submit")
@click.option("--dry-run", is_flag=True, default=False,
              help="Validate readiness without actually submitting")
def submit_cmd(run_id: str, dry_run: bool):
    """
    Submit one approved claim to its payer portal.

    The claim must be in 'approved' or 're_processed' status in the
    review queue. Submission uses the payer-specific adapter (registered
    in payer_submission_registry) or the global default adapter.

    Retries up to submission_max_retries times with exponential backoff
    on transient network/5xx failures (configured in settings).

    Examples:\n
        rcm-denial submit --run-id <run_id>\n
        rcm-denial submit --run-id <run_id> --dry-run
    """
    from rcm_denial.services.review_queue import get_queue_item

    item = get_queue_item(run_id)
    if not item:
        console.print(f"[red]run_id {run_id!r} not found in review queue.[/red]")
        sys.exit(1)

    status = item["status"]
    if status not in ("approved", "re_processed"):
        console.print(
            f"[red]Cannot submit: claim status is '{status}'.[/red]\n"
            "Approve first: [cyan]rcm-denial review-queue approve --run-id "
            f"{run_id}[/cyan]"
        )
        sys.exit(1)

    if dry_run:
        console.print(Panel.fit(
            f"[bold yellow]Dry Run — Submission Readiness Check[/bold yellow]\n"
            f"Claim: [cyan]{item['claim_id']}[/cyan]   "
            f"Status: [green]{status}[/green]\n"
            f"Payer: {item.get('payer_id') or 'unknown'}   "
            f"Package: {item.get('package_type') or '-'}",
            border_style="yellow",
        ))
        console.print("[green]✓[/green] Claim is ready for submission.")
        return

    from rcm_denial.services.submission_service import submit_approved_claim

    console.print(Panel.fit(
        f"[bold blue]Submitting Claim[/bold blue]\n"
        f"Claim: [cyan]{item['claim_id']}[/cyan]   "
        f"Payer: {item.get('payer_id') or 'unknown'}\n"
        f"Run ID: [dim]{run_id}[/dim]",
        border_style="blue",
    ))

    try:
        result = submit_approved_claim(run_id)
    except ValueError as exc:
        console.print(f"[red]Submission error:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Unexpected error:[/red] {exc}")
        raise

    if result.success:
        console.print(
            f"[green]✓[/green] Submitted successfully via "
            f"[bold]{result.submission_method}[/bold].\n"
            f"  Confirmation: [cyan]{result.confirmation_number}[/cyan]\n"
            f"  Response:     {result.response_message}"
        )
    else:
        console.print(
            f"[red]✗[/red] Submission failed.\n"
            f"  Code:    [yellow]{result.response_code}[/yellow]\n"
            f"  Message: {result.response_message}"
        )
        sys.exit(1)


# ------------------------------------------------------------------ #
# Command: submit-batch
# ------------------------------------------------------------------ #

@cli.command("submit-batch")
@click.option("--batch-id", required=True, help="Batch ID to submit all approved claims for")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be submitted without actually submitting")
def submit_batch_cmd(batch_id: str, dry_run: bool):
    """
    Submit all approved claims in a batch to their payer portals.

    Processes every claim with status 'approved' in the batch.
    Continues on individual claim failure — never aborts the batch.
    Each claim uses its own payer adapter and retry policy.

    Examples:\n
        rcm-denial submit-batch --batch-id BATCH-2024-001\n
        rcm-denial submit-batch --batch-id BATCH-2024-001 --dry-run
    """
    from rcm_denial.services.review_queue import get_queue

    approved = get_queue(batch_id=batch_id, status="approved")

    if not approved:
        console.print(
            f"[yellow]No approved claims found for batch [cyan]{batch_id}[/cyan].[/yellow]\n"
            "Approve claims first: [cyan]rcm-denial review-queue approve-all "
            f"--batch-id {batch_id}[/cyan]"
        )
        return

    console.print(Panel.fit(
        f"[bold blue]Batch Submission[/bold blue]\n"
        f"Batch: [cyan]{batch_id}[/cyan]   "
        f"Approved claims: [bold]{len(approved)}[/bold]"
        + ("   [yellow](DRY RUN)[/yellow]" if dry_run else ""),
        border_style="blue",
    ))

    if dry_run:
        table = Table(title="Claims Ready for Submission", show_header=True, header_style="bold magenta")
        table.add_column("Claim ID", style="cyan")
        table.add_column("Run ID",   style="dim")
        table.add_column("Payer",    style="yellow")
        table.add_column("Amount",   justify="right")
        table.add_column("Package",  style="dim")

        for item in approved:
            table.add_row(
                item["claim_id"],
                item["run_id"][:12] + "...",
                item.get("payer_id") or "-",
                f"${item['billed_amount']:,.0f}" if item.get("billed_amount") else "-",
                item.get("package_type") or "-",
            )
        console.print(table)
        console.print(f"\n[yellow]Dry run complete.[/yellow] {len(approved)} claim(s) would be submitted.")
        return

    from rcm_denial.services.submission_service import submit_approved_batch

    try:
        summary = submit_approved_batch(batch_id)
    except Exception as exc:
        console.print(f"[red]Batch submission error:[/red] {exc}")
        raise

    # Summary table
    stats_table = Table(title="Batch Submission Summary", show_header=False, box=None, padding=(0, 2))
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value")
    stats_table.add_row("Batch ID",  summary["batch_id"])
    stats_table.add_row("Submitted", f"[green]{summary['submitted']}[/green]")
    stats_table.add_row("Failed",    f"[red]{summary['failed']}[/red]")
    stats_table.add_row("Skipped",   str(summary["skipped"]))
    console.print(stats_table)

    # Per-claim results
    if summary["results"]:
        detail = Table(title="Per-Claim Submission Results", show_header=True)
        detail.add_column("Claim ID",     style="cyan")
        detail.add_column("Run ID",       style="dim")
        detail.add_column("Status",       no_wrap=True)
        detail.add_column("Method",       style="dim")
        detail.add_column("Confirmation", style="green")
        detail.add_column("Message")

        for r in summary["results"]:
            if r.get("success"):
                status_cell = "[green]submitted[/green]"
            else:
                status_cell = "[red]failed[/red]"
            detail.add_row(
                r.get("claim_id") or "-",
                (r.get("run_id") or "")[:12] + "...",
                status_cell,
                r.get("method") or r.get("error", "")[:20] or "-",
                r.get("confirmation_number") or "-",
                (r.get("message") or r.get("error") or "")[:60],
            )
        console.print(detail)

    if summary["failed"] == 0:
        console.print(f"\n[green]✓[/green] All {summary['submitted']} claim(s) submitted successfully.")
    else:
        console.print(
            f"\n[yellow]⚠[/yellow] {summary['submitted']} submitted, "
            f"[red]{summary['failed']} failed[/red]. "
            "Check logs or run [cyan]rcm-denial submission-log --run-id <id>[/cyan] per failed claim."
        )


# ------------------------------------------------------------------ #
# Command: submission-status
# ------------------------------------------------------------------ #

@cli.command("submission-status")
@click.option("--run-id", required=True, help="Run ID of a submitted claim")
def submission_status_cmd(run_id: str):
    """
    Poll the payer for adjudication status of a submitted claim.

    Reads the confirmation number from the submission log and calls
    the payer adapter's check_status() endpoint.

    Examples:\n
        rcm-denial submission-status --run-id <run_id>
    """
    from rcm_denial.services.submission_service import check_submission_status

    console.print(f"[dim]Checking adjudication status for run_id: {run_id}[/dim]")

    try:
        status = check_submission_status(run_id)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Unexpected error:[/red] {exc}")
        raise

    PAYER_STATUS_COLORS = {
        "received":     "cyan",
        "in_review":    "yellow",
        "adjudicated":  "green",
        "rejected":     "red",
        "unknown":      "dim",
    }
    sc = PAYER_STATUS_COLORS.get(status.payer_status, "white")

    console.print(Panel.fit(
        f"[bold blue]Adjudication Status[/bold blue]\n"
        f"Confirmation: [cyan]{status.confirmation_number}[/cyan]\n"
        f"Payer status: [{sc}]{status.payer_status.upper()}[/{sc}]\n"
        + (f"Adjudicated: {status.adjudication_date}\n" if status.adjudication_date else "")
        + (f"Paid amount: [green]${status.paid_amount:,.2f}[/green]\n" if status.paid_amount is not None else "")
        + (f"Denial upheld: [red]{status.denial_upheld}[/red]\n" if status.denial_upheld is not None else "")
        + (f"Notes: {status.payer_notes}" if status.payer_notes else ""),
        border_style="blue",
    ))


# ------------------------------------------------------------------ #
# Command: submission-log
# ------------------------------------------------------------------ #

@cli.command("submission-log")
@click.option("--run-id", required=True, help="Run ID to show submission attempts for")
def submission_log_cmd(run_id: str):
    """
    Show all submission attempts for a claim (newest first).

    Each row = one attempt. Use this to debug failed submissions,
    see confirmation numbers, or review retry history.

    Examples:\n
        rcm-denial submission-log --run-id <run_id>
    """
    from rcm_denial.services.submission_service import get_submission_log

    logs = get_submission_log(run_id)

    if not logs:
        console.print(
            f"[yellow]No submission attempts found for run_id {run_id!r}.[/yellow]\n"
            "Submit first: [cyan]rcm-denial submit --run-id "
            f"{run_id}[/cyan]"
        )
        return

    console.print(Panel.fit(
        f"[bold blue]Submission Log[/bold blue]   Run ID: [dim]{run_id}[/dim]",
        border_style="blue",
    ))

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("#",            style="dim",    justify="right")
    table.add_column("Status",       no_wrap=True)
    table.add_column("Method",       style="cyan")
    table.add_column("Response Code",style="yellow")
    table.add_column("Confirmation", style="green")
    table.add_column("Submitted At", style="dim")
    table.add_column("Message")

    for row in logs:
        status_color = "green" if row["status"] == "submitted" else "red"
        table.add_row(
            str(row["attempt_number"]),
            f"[{status_color}]{row['status']}[/{status_color}]",
            row.get("submission_method") or "-",
            row.get("response_code") or "-",
            row.get("confirmation_number") or "-",
            str(row.get("submitted_at") or "-"),
            (row.get("response_message") or "")[:60],
        )

    console.print(table)
    console.print(f"\n[dim]{len(logs)} attempt(s) total[/dim]")


# ------------------------------------------------------------------ #
# Command group: submission-registry
# ------------------------------------------------------------------ #

@cli.group("submission-registry")
def submission_registry_grp():
    """
    Manage the payer submission method registry.

    Each payer can have a specific submission method (API, RPA portal,
    EDI 837) registered here. Claims use this registry to select the
    right adapter at submission time.

    Examples:\n
        rcm-denial submission-registry list\n
        rcm-denial submission-registry register --payer-id BCBS --method availity_api\n
        rcm-denial submission-registry register --payer-id UHC --method rpa_portal \\
            --portal-url https://provider.uhc.com
    """
    pass


@submission_registry_grp.command("list")
def submission_registry_list_cmd():
    """List all registered payer submission methods."""
    from rcm_denial.services.submission_adapters import list_payer_submission_registry

    entries = list_payer_submission_registry()

    if not entries:
        console.print(
            "[yellow]No payer submission methods registered.[/yellow]\n"
            "Add one: [cyan]rcm-denial submission-registry register "
            "--payer-id <id> --method <method>[/cyan]"
        )
        return

    table = Table(
        title="Payer Submission Registry",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Payer ID",      style="cyan")
    table.add_column("Method",        style="yellow")
    table.add_column("API Endpoint",  style="dim")
    table.add_column("Portal URL",    style="dim")
    table.add_column("Clearinghouse", style="dim")
    table.add_column("Notes",         style="dim")

    for e in entries:
        table.add_row(
            e.get("payer_id") or "-",
            e.get("submission_method") or "-",
            (e.get("api_endpoint") or "-")[:40],
            (e.get("portal_url") or "-")[:40],
            e.get("clearinghouse_id") or "-",
            (e.get("notes") or "-")[:40],
        )

    console.print(table)
    console.print(f"\n[dim]{len(entries)} payer(s) registered[/dim]")
    console.print(
        "[dim]Unregistered payers fall back to the global "
        f"submission_adapter setting (current default shown in .env)[/dim]"
    )


@submission_registry_grp.command("register")
@click.option("--payer-id", required=True, help="Payer ID (e.g. BCBS, UHC, Aetna)")
@click.option(
    "--method", required=True,
    type=click.Choice(["availity_api", "rpa_portal", "edi_837", "mock"], case_sensitive=False),
    help="Submission adapter to use for this payer",
)
@click.option("--api-endpoint", default="", help="REST API base URL (for availity_api)")
@click.option("--portal-url",   default="", help="Web portal URL (for rpa_portal)")
@click.option("--clearinghouse", "clearinghouse_id", default="",
              help="Clearinghouse/SFTP host (for edi_837)")
@click.option("--notes", default="", help="Free-text notes about this registration")
def submission_registry_register_cmd(
    payer_id: str,
    method: str,
    api_endpoint: str,
    portal_url: str,
    clearinghouse_id: str,
    notes: str,
):
    """
    Register (or update) a payer's submission method.

    This upserts the record — running it again updates an existing entry.

    Examples:\n
        rcm-denial submission-registry register --payer-id BCBS --method availity_api\n
        rcm-denial submission-registry register --payer-id UHC --method rpa_portal \\
            --portal-url https://provider.uhc.com/appeals\n
        rcm-denial submission-registry register --payer-id Medicare --method edi_837 \\
            --clearinghouse change_healthcare
    """
    from rcm_denial.services.submission_adapters import register_payer_submission

    register_payer_submission(
        payer_id=payer_id,
        submission_method=method,
        api_endpoint=api_endpoint,
        portal_url=portal_url,
        clearinghouse_id=clearinghouse_id,
        notes=notes,
    )
    console.print(
        f"[green]✓[/green] Registered [cyan]{payer_id}[/cyan] → "
        f"[bold yellow]{method}[/bold yellow]"
        + (f"  endpoint: {api_endpoint}" if api_endpoint else "")
        + (f"  portal: {portal_url}" if portal_url else "")
        + (f"  clearinghouse: {clearinghouse_id}" if clearinghouse_id else "")
    )


# ------------------------------------------------------------------ #
# Command: submission-stats
# ------------------------------------------------------------------ #

@cli.command("submission-stats")
@click.option("--batch-id", default="", help="Filter by batch ID (default: all-time)")
def submission_stats_cmd(batch_id: str):
    """
    Show aggregated submission metrics.

    Reports attempt counts and success/failure rates per batch or
    across all time. Use after a batch submission to confirm outcomes.

    Examples:\n
        rcm-denial submission-stats\n
        rcm-denial submission-stats --batch-id BATCH-2024-001
    """
    from rcm_denial.services.submission_service import get_submission_stats

    stats = get_submission_stats(batch_id=batch_id)

    title = (
        f"Submission Stats — Batch {batch_id}"
        if batch_id else
        "Submission Stats — All Time"
    )
    console.print(Panel.fit(f"[bold blue]{title}[/bold blue]", border_style="blue"))

    status_counts = stats.get("status_counts", {})

    if not status_counts:
        console.print("[yellow]No submission records found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Status",         style="cyan")
    table.add_column("Count",          justify="right", style="bold")
    table.add_column("Total Attempts", justify="right", style="dim")
    table.add_column("Avg Attempts",   justify="right", style="dim")

    total_claims = 0
    for status_val, data in sorted(status_counts.items()):
        count    = data["count"]
        attempts = data["total_attempts"] or 0
        avg      = f"{attempts / count:.1f}" if count else "-"
        color    = "green" if status_val == "submitted" else "red"
        table.add_row(
            f"[{color}]{status_val}[/{color}]",
            str(count),
            str(attempts),
            avg,
        )
        total_claims += count

    console.print(table)
    console.print(f"\n[dim]{total_claims} total submission record(s)[/dim]")


# ------------------------------------------------------------------ #
# Command: stats  (Gap 47 — comprehensive observability dashboard)
# ------------------------------------------------------------------ #

@cli.command("stats")
@click.option("--batch-id", default="", help="Filter by batch ID (default: all-time)")
@click.option("--export-metrics", is_flag=True, default=False,
              help="Write data/metrics/rcm_denial.prom after displaying stats")
@click.option("--push-gateway", default="",
              help="Push metrics to Prometheus Pushgateway URL (e.g. http://localhost:9091)")
def stats_cmd(batch_id: str, export_metrics: bool, push_gateway: str):
    """
    Comprehensive pipeline statistics — the single-pane view.

    Shows: pipeline success/failure, LLM cost breakdown, review queue
    summary, submission outcomes, write-off revenue impact, and
    processing duration percentiles.

    Optionally exports Prometheus metrics after display.

    Examples:\n
        rcm-denial stats\n
        rcm-denial stats --batch-id BATCH-2024-001\n
        rcm-denial stats --export-metrics\n
        rcm-denial stats --push-gateway http://localhost:9091
    """
    from rcm_denial.services.metrics_service import get_current_metrics, collect_and_export, push_to_gateway
    from rcm_denial.services.cost_tracker import get_batch_cost_summary

    label = f"Batch {batch_id}" if batch_id else "All Time"
    console.print(Panel.fit(
        f"[bold blue]RCM Denial Management — Statistics[/bold blue]\n"
        f"[dim]{label}[/dim]",
        border_style="blue",
    ))

    try:
        m = get_current_metrics(batch_id=batch_id)
    except Exception as exc:
        console.print(f"[red]Failed to collect metrics:[/red] {exc}")
        raise

    # ── Pipeline ─────────────────────────────────────────────────────
    console.print("\n[bold cyan]Pipeline Results[/bold cyan]")
    pip_table = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 2))
    pip_table.add_column("Status",         style="cyan")
    pip_table.add_column("Package Type",   style="dim")
    pip_table.add_column("Count",          justify="right", style="bold")
    pip_table.add_column("Avg Duration",   justify="right", style="dim")
    pip_table.add_column("LLM Calls",      justify="right", style="dim")

    total_claims = 0
    for key, data in sorted(m["pipeline"].items()):
        parts  = key.split("_", 1)
        status = parts[0]
        pkg    = parts[1] if len(parts) > 1 else "-"
        color  = "green" if status == "complete" else ("yellow" if status == "partial" else "red")
        pip_table.add_row(
            f"[{color}]{status}[/{color}]",
            pkg,
            str(data["count"]),
            f"{data['avg_duration_ms']:.0f} ms",
            str(data["total_llm_calls"]),
        )
        total_claims += data["count"]

    if total_claims == 0:
        console.print("[yellow]  No pipeline results found.[/yellow]")
    else:
        console.print(pip_table)
        d = m["duration_ms"]
        console.print(
            f"  [dim]Duration p50={d['p50']:.0f}ms  p95={d['p95']:.0f}ms  "
            f"p99={d['p99']:.0f}ms  avg={d['avg']:.0f}ms[/dim]"
        )

    # ── LLM Cost ─────────────────────────────────────────────────────
    console.print("\n[bold cyan]LLM Cost (Gap 49)[/bold cyan]")
    cost_data = m["llm_cost"]
    if cost_data["total_calls"] == 0:
        console.print("  [dim]No LLM calls recorded yet.[/dim]")
    else:
        cost_table = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 2))
        cost_table.add_column("Model",           style="cyan")
        cost_table.add_column("Calls",           justify="right")
        cost_table.add_column("Input Tokens",    justify="right", style="dim")
        cost_table.add_column("Output Tokens",   justify="right", style="dim")
        cost_table.add_column("Cost (USD)",      justify="right", style="bold green")

        for model, d in sorted(cost_data["by_model"].items()):
            cost_table.add_row(
                model,
                str(d["calls"]),
                f"{d['input_tokens']:,}",
                f"{d['output_tokens']:,}",
                f"${d['cost_usd']:.6f}",
            )
        console.print(cost_table)

        try:
            batch_cost = get_batch_cost_summary(batch_id=batch_id)
            console.print(
                f"  Total: [bold green]${batch_cost['total_cost_usd']:.6f}[/bold green]   "
                f"Claims tracked: {batch_cost['claims_tracked']}   "
                f"Avg/claim: ${batch_cost['avg_cost_per_claim']:.6f}"
            )
        except Exception:
            pass

    # ── Review Queue ─────────────────────────────────────────────────
    console.print("\n[bold cyan]Review Queue[/bold cyan]")
    queue = m["review_queue"]
    if not queue:
        console.print("  [dim]No queue entries found.[/dim]")
    else:
        q_table = Table(show_header=False, box=None, padding=(0, 2))
        q_table.add_column("Status", style="cyan")
        q_table.add_column("Count",  justify="right", style="bold")
        STATUS_COLORS = {
            "pending": "yellow", "approved": "green", "submitted": "green",
            "re_routed": "blue", "re_processed": "cyan",
            "human_override": "magenta", "written_off": "red",
        }
        for status, cnt in sorted(queue.items()):
            color = STATUS_COLORS.get(status, "white")
            q_table.add_row(f"[{color}]{status}[/{color}]", str(cnt))
        console.print(q_table)

    # ── Submissions ──────────────────────────────────────────────────
    console.print("\n[bold cyan]Submissions[/bold cyan]")
    subs = m["submissions"]
    if not subs:
        console.print("  [dim]No submission records found.[/dim]")
    else:
        s_table = Table(show_header=False, box=None, padding=(0, 2))
        s_table.add_column("Status/Method", style="cyan")
        s_table.add_column("Count",         justify="right", style="bold")
        for key, cnt in sorted(subs.items()):
            parts  = key.split("_", 1)
            status = parts[0]
            method = parts[1] if len(parts) > 1 else "-"
            color  = "green" if status == "submitted" else "red"
            s_table.add_row(f"[{color}]{status}[/{color}] via {method}", str(cnt))
        console.print(s_table)

    # ── Write-offs ───────────────────────────────────────────────────
    wo = m["write_offs"]
    if wo["total_count"] > 0:
        console.print(
            f"\n[bold red]Write-Off Revenue Impact[/bold red]\n"
            f"  Count: [bold red]{wo['total_count']}[/bold red]   "
            f"Revenue lost: [bold red]${wo['total_amount_usd']:,.2f}[/bold red]"
        )
        for reason, data in wo["by_reason"].items():
            console.print(f"  • {reason}: {data['count']} claim(s) / ${data['amount_usd']:,.2f}")
    else:
        console.print("\n[green]✓[/green] Write-offs: [bold green]0[/bold green] (target achieved)")

    # ── Eval Quality Signals ─────────────────────────────────────────
    console.print("\n[bold cyan]Eval Quality Signals[/bold cyan]")
    try:
        from rcm_denial.services.review_queue import get_review_stats
        rq_stats = get_review_stats()
        fp_rate   = rq_stats.get("first_pass_approval_rate_pct", None)
        ov_rate   = rq_stats.get("override_rate_pct", None)
        reroute   = rq_stats.get("reroute_by_stage", {})
        calib     = rq_stats.get("confidence_calibration", {})
        mc_count  = len(rq_stats.get("multi_cycle_claim_ids", []))

        if fp_rate is None and ov_rate is None:
            console.print("  [dim]No review queue data yet — run some claims through the pipeline.[/dim]")
        else:
            eq_table = Table(show_header=False, box=None, padding=(0, 2))
            eq_table.add_column("Metric", style="cyan")
            eq_table.add_column("Value",  justify="right", style="bold")

            if fp_rate is not None:
                color = "green" if fp_rate >= 70 else ("yellow" if fp_rate >= 50 else "red")
                eq_table.add_row(
                    "First-pass approval rate",
                    f"[{color}]{fp_rate:.1f}%[/{color}]",
                )
            if ov_rate is not None:
                color = "green" if ov_rate <= 5 else ("yellow" if ov_rate <= 15 else "red")
                eq_table.add_row(
                    "Human override rate  [dim](lower = better)[/dim]",
                    f"[{color}]{ov_rate:.1f}%[/{color}]",
                )
            if mc_count > 0:
                eq_table.add_row(
                    "Multi-cycle claims  [dim](eval dataset)[/dim]",
                    str(mc_count),
                )
            if calib:
                gap = calib.get("gap", 0.0)
                color = "green" if gap and gap > 0 else "yellow"
                eq_table.add_row(
                    "Confidence calibration gap  [dim](+ = well-calibrated)[/dim]",
                    f"[{color}]{gap:+.3f}[/{color}]" if gap is not None else "[dim]N/A[/dim]",
                )
            console.print(eq_table)

            if reroute:
                top = sorted(reroute.items(), key=lambda x: x[1], reverse=True)[:3]
                console.print(
                    "  Re-route hotspots: " +
                    "  ".join(f"[yellow]{stage}[/yellow]={cnt}" for stage, cnt in top)
                )
    except Exception as exc:
        console.print(f"  [dim]Eval signals unavailable: {exc}[/dim]")

    # ── Prometheus export ────────────────────────────────────────────
    if export_metrics or push_gateway:
        console.print()
        try:
            prom_path = collect_and_export(batch_id=batch_id)
            console.print(f"[green]✓[/green] Metrics written to [dim]{prom_path}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]⚠[/yellow] Metrics export failed: {exc}")

        if push_gateway:
            try:
                push_to_gateway(push_gateway, batch_id=batch_id)
                console.print(f"[green]✓[/green] Metrics pushed to Pushgateway: {push_gateway}")
            except Exception as exc:
                console.print(f"[yellow]⚠[/yellow] Push failed: {exc}")

    console.print(
        f"\n[dim]Grafana dashboard: data/observability/grafana_dashboard.json  "
        f"| Prometheus: data/observability/prometheus.yml[/dim]"
    )


# ------------------------------------------------------------------ #
# Command group: db  (Gap 48 — database backend management)
# ------------------------------------------------------------------ #

@cli.group("db")
def db_grp():
    """
    Database backend management.

    Default backend is SQLite (zero-config). Switch to PostgreSQL
    for production by setting DATABASE_URL in .env.

    Examples:\n
        rcm-denial db info\n
        rcm-denial db export-schema > data/schema.sql\n
        rcm-denial db migrate-to-postgres
    """
    pass


@db_grp.command("info")
def db_info_cmd():
    """
    Show current database backend and connection details.

    Examples:\n
        rcm-denial db info
    """
    from rcm_denial.services.db_backend import get_db_type, get_placeholder
    from rcm_denial.config.settings import settings

    db_type = get_db_type()
    console.print(Panel.fit(
        f"[bold blue]Database Backend[/bold blue]\n"
        f"Type:        [cyan]{db_type}[/cyan]\n"
        + (
            f"Path:        [dim]{settings.data_dir / 'rcm_denial.db'}[/dim]"
            if db_type == "sqlite"
            else f"URL:         [dim]{settings.database_url[:40]}...[/dim]"
        ),
        border_style="blue",
    ))

    if db_type == "sqlite":
        console.print(
            "\nTo switch to [bold]PostgreSQL[/bold] for production:\n"
            "  1. Add to [cyan].env[/cyan]:\n"
            "       DATABASE_TYPE=postgresql\n"
            "       DATABASE_URL=postgresql://user:pass@host:5432/rcm_denial\n\n"
            "  2. Create schema: [cyan]rcm-denial db export-schema | psql -d rcm_denial[/cyan]\n\n"
            "  3. Migrate data:  [cyan]rcm-denial db migrate-to-postgres[/cyan]"
        )


@db_grp.command("export-schema")
@click.option("--output", default="", help="Write to file instead of stdout")
def db_export_schema_cmd(output: str):
    """
    Print the PostgreSQL DDL schema to stdout (or a file).

    Pipe directly into psql to create tables on the target server:

        rcm-denial db export-schema | psql -U postgres -d rcm_denial

    Examples:\n
        rcm-denial db export-schema\n
        rcm-denial db export-schema --output data/schema.sql
    """
    from rcm_denial.services.db_backend import export_schema_sql

    sql = export_schema_sql()
    if output:
        Path(output).write_text(sql, encoding="utf-8")
        console.print(f"[green]✓[/green] Schema written to [dim]{output}[/dim]")
    else:
        console.print(sql)


@db_grp.command("migrate-to-postgres")
def db_migrate_cmd():
    """
    One-time migration: copy all SQLite data to PostgreSQL.

    Prerequisites:\n
        - DATABASE_URL and DATABASE_TYPE=postgresql set in .env\n
        - PostgreSQL schema already created (see export-schema)\n
        - pip install psycopg2-binary

    Examples:\n
        rcm-denial db migrate-to-postgres
    """
    from rcm_denial.services.db_backend import get_db_type, migrate_sqlite_to_postgres

    if get_db_type() != "postgresql":
        console.print(
            "[red]Error:[/red] DATABASE_TYPE must be 'postgresql' and DATABASE_URL must be set.\n"
            "Run [cyan]rcm-denial db info[/cyan] for setup instructions."
        )
        sys.exit(1)

    console.print("[blue]Starting SQLite → PostgreSQL migration...[/blue]")
    try:
        counts = migrate_sqlite_to_postgres()
    except Exception as exc:
        console.print(f"[red]Migration failed:[/red] {exc}")
        raise

    table = Table(title="Migration Results", show_header=True, header_style="bold magenta")
    table.add_column("Table", style="cyan")
    table.add_column("Rows Migrated", justify="right", style="green")

    for tbl, cnt in counts.items():
        table.add_row(tbl, str(cnt))

    console.print(table)
    console.print("\n[green]✓[/green] Migration complete.")


# ------------------------------------------------------------------ #
# Command group: evals  — criteria checks & golden dataset regression
# ------------------------------------------------------------------ #

@cli.group("evals")
def evals_grp():
    """
    Evaluation commands — criteria checks and golden dataset regression.

    Commands:\n
        rcm-denial evals run              — run golden dataset checks\n
        rcm-denial evals check-output     — check a single pipeline output\n
        rcm-denial evals quality-signals  — show review-queue quality signals
    """
    pass


@evals_grp.command("run")
@click.option(
    "--golden-cases",
    default="data/evals/golden_cases.json",
    help="Path to golden_cases.json (default: data/evals/golden_cases.json)",
)
@click.option(
    "--output-dir",
    default="",
    help="Pipeline output dir to load actual LLM outputs from (optional)",
)
@click.option(
    "--json-out",
    default="",
    help="Write full report as JSON to this file path (optional)",
)
def evals_run_cmd(golden_cases: str, output_dir: str, json_out: str):
    """
    Run criteria checks against the golden dataset.

    Without --output-dir: validates structural consistency of each golden case
    definition (self-check).

    With --output-dir: loads actual pipeline output (submission_metadata.json)
    for each golden case and checks predicted vs expected action/category plus
    all structural assertions.

    Examples:\n
        rcm-denial evals run\n
        rcm-denial evals run --output-dir ./output\n
        rcm-denial evals run --output-dir ./output --json-out data/evals/last_report.json
    """
    from rcm_denial.evals.criteria_checks import run_golden_checks

    gc_path = Path(golden_cases)
    out_dir = Path(output_dir) if output_dir else None

    console.print(Panel.fit(
        "[bold blue]RCM Denial Evals — Golden Dataset Regression[/bold blue]\n"
        f"[dim]{gc_path}[/dim]",
        border_style="blue",
    ))

    try:
        report = run_golden_checks(golden_cases_path=gc_path, output_dir=out_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    # ── Summary panel ──────────────────────────────────────────────
    action_pct   = round(report.action_accuracy * 100, 1)
    category_pct = round(report.category_accuracy * 100, 1)
    score        = report.avg_composite_score

    a_color = "green" if action_pct >= 80 else ("yellow" if action_pct >= 60 else "red")
    c_color = "green" if category_pct >= 80 else ("yellow" if category_pct >= 60 else "red")
    s_color = "green" if score >= 0.75 else ("yellow" if score >= 0.55 else "red")

    console.print(f"\n[bold]Results: {report.passed_cases}/{report.total_cases} cases fully passed[/bold]")
    console.print(
        f"  Action accuracy:   [{a_color}]{action_pct}%[/{a_color}]\n"
        f"  Category accuracy: [{c_color}]{category_pct}%[/{c_color}]\n"
        f"  Avg composite:     [{s_color}]{score:.3f}[/{s_color}]"
    )

    # ── Per-case table ─────────────────────────────────────────────
    table = Table(
        show_header=True, header_style="bold magenta",
        box=None, padding=(0, 2),
    )
    table.add_column("Case ID",          style="dim")
    table.add_column("Category",         style="cyan")
    table.add_column("Expected Action",  style="dim")
    table.add_column("Predicted Action", style="dim")
    table.add_column("Act?",             justify="center")
    table.add_column("Struct?",          justify="center")
    table.add_column("Score",            justify="right", style="bold")

    for r in report.case_results:
        act_ok   = "[green]✓[/green]" if r.action_match else "[red]✗[/red]"
        struct_ok = (
            "[green]✓[/green]"
            if (r.analysis_suite is None or r.analysis_suite.passed)
            else "[red]✗[/red]"
        )
        score_color = "green" if r.composite_score >= 0.75 else "yellow"
        table.add_row(
            r.case_id,
            r.denial_category,
            r.expected_action,
            r.predicted_action or "[dim]N/A[/dim]",
            act_ok,
            struct_ok,
            f"[{score_color}]{r.composite_score:.3f}[/{score_color}]",
        )

    console.print()
    console.print(table)

    # ── Failures ───────────────────────────────────────────────────
    if report.failures:
        console.print(f"\n[yellow]⚠  {len(report.failures)} load warning(s):[/yellow]")
        for msg in report.failures[:5]:
            console.print(f"   {msg}")
        if len(report.failures) > 5:
            console.print(f"   ... and {len(report.failures) - 5} more")

    # ── Structural check failures ──────────────────────────────────
    failed_structs = [
        r for r in report.case_results
        if r.analysis_suite and not r.analysis_suite.passed
    ]
    if failed_structs:
        console.print(f"\n[red]Structural check failures ({len(failed_structs)} cases):[/red]")
        for r in failed_structs:
            for check_name in r.analysis_suite.failed_checks:
                console.print(f"  [dim]{r.case_id}[/dim] → [red]{check_name}[/red]")

    # ── JSON export ────────────────────────────────────────────────
    if json_out:
        import json as _json
        json_path = Path(json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            _json.dump(report.to_dict(), f, indent=2)
        console.print(f"\n[green]✓[/green] Report written to [dim]{json_out}[/dim]")


@evals_grp.command("check-output")
@click.argument("claim_id")
@click.option(
    "--output-dir",
    default="./output",
    help="Pipeline output base directory (default: ./output)",
)
def evals_check_output_cmd(claim_id: str, output_dir: str):
    """
    Run criteria checks on a single claim's pipeline output.

    Loads submission_metadata.json from output/<CLAIM_ID>/ and
    runs all structural assertions, printing a pass/fail report.

    Examples:\n
        rcm-denial evals check-output CLM-20240101-001\n
        rcm-denial evals check-output CLM-001 --output-dir ./output
    """
    import json as _json
    from rcm_denial.evals.criteria_checks import (
        check_denial_analysis, check_appeal_letter, check_evidence_result,
    )

    meta_path = Path(output_dir) / claim_id / "submission_metadata.json"
    if not meta_path.exists():
        console.print(f"[red]Error:[/red] No output found at [dim]{meta_path}[/dim]")
        sys.exit(1)

    with open(meta_path) as f:
        meta = _json.load(f)

    console.print(Panel.fit(
        f"[bold blue]Criteria Check — {claim_id}[/bold blue]",
        border_style="blue",
    ))

    # Analysis
    analysis_suite = check_denial_analysis(meta)
    _print_suite(console, analysis_suite)

    # Appeal letter if present
    if "appeal_letter" in meta:
        appeal_suite = check_appeal_letter(meta["appeal_letter"])
        _print_suite(console, appeal_suite)

    # Evidence check if present
    if "evidence_check" in meta:
        ev_suite = check_evidence_result(meta["evidence_check"])
        _print_suite(console, ev_suite)

    overall = "PASS" if analysis_suite.passed else "FAIL"
    color   = "green" if analysis_suite.passed else "red"
    console.print(f"\nOverall: [{color}]{overall}[/{color}]  score={analysis_suite.score:.3f}")


def _print_suite(console, suite) -> None:
    """Print a CheckSuite result to the console."""
    status = "[green]PASS[/green]" if suite.passed else "[red]FAIL[/red]"
    console.print(f"\n[bold]{suite.subject}[/bold]  {status}  (score={suite.score:.3f})")
    for r in suite.results:
        icon = "[green]✓[/green]" if r.passed else "[red]✗[/red]"
        console.print(f"  {icon}  {r.check_name}")
        for issue in r.issues:
            console.print(f"       [yellow]→ {issue}[/yellow]")


@evals_grp.command("quality-signals")
def evals_quality_signals_cmd():
    """
    Show review-queue quality signals (eval ground truth from reviewer actions).

    Displays: first-pass approval rate, override rate, re-route hotspots,
    confidence calibration gap, and multi-cycle claim IDs for manual review.

    Examples:\n
        rcm-denial evals quality-signals
    """
    from rcm_denial.services.review_queue import get_review_stats

    console.print(Panel.fit(
        "[bold blue]Review Queue — Eval Quality Signals[/bold blue]",
        border_style="blue",
    ))

    try:
        stats = get_review_stats()
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    fp_rate  = stats.get("first_pass_approval_rate_pct")
    ov_rate  = stats.get("override_rate_pct")
    reroute  = stats.get("reroute_by_stage", {})
    calib    = stats.get("confidence_calibration", {})
    mc_ids   = stats.get("multi_cycle_claim_ids", [])
    total    = stats.get("total_in_queue", 0)

    if total == 0:
        console.print("\n[yellow]No review queue data yet.[/yellow]")
        console.print("[dim]Run some claims through the pipeline first.[/dim]")
        return

    # Main metrics
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value",  justify="right")
    table.add_column("Signal", style="dim")

    if fp_rate is not None:
        color  = "green" if fp_rate >= 70 else ("yellow" if fp_rate >= 50 else "red")
        signal = "Good" if fp_rate >= 70 else ("Investigate" if fp_rate >= 50 else "Action needed")
        table.add_row("First-pass approval rate", f"[{color}]{fp_rate:.1f}%[/{color}]", signal)

    if ov_rate is not None:
        color  = "green" if ov_rate <= 5 else ("yellow" if ov_rate <= 15 else "red")
        signal = "Good" if ov_rate <= 5 else ("High" if ov_rate <= 15 else "Critical")
        table.add_row("Human override rate", f"[{color}]{ov_rate:.1f}%[/{color}]", signal)

    table.add_row("Total in queue", str(total), "")
    table.add_row("Multi-cycle claims", str(len(mc_ids)), "natural eval dataset")

    console.print(table)

    # Calibration
    if calib:
        console.print("\n[bold cyan]Confidence Calibration[/bold cyan]")
        avg_approved = calib.get("avg_conf_approved_first_pass")
        avg_rerouted = calib.get("avg_conf_rerouted")
        gap          = calib.get("gap")
        note         = calib.get("note", "")

        cal_table = Table(show_header=False, box=None, padding=(0, 2))
        cal_table.add_column("Label",  style="dim")
        cal_table.add_column("Value",  justify="right")
        if avg_approved is not None:
            cal_table.add_row("Avg confidence (approved, first-pass)", f"{avg_approved:.3f}")
        if avg_rerouted is not None:
            cal_table.add_row("Avg confidence (re-routed)",           f"{avg_rerouted:.3f}")
        if gap is not None:
            color = "green" if gap > 0 else "red"
            cal_table.add_row(
                "Calibration gap  (+ = well-calibrated)",
                f"[{color}]{gap:+.3f}[/{color}]",
            )
        console.print(cal_table)
        if note:
            console.print(f"  [dim]{note}[/dim]")

    # Re-route hotspots
    if reroute:
        console.print("\n[bold cyan]Re-route Hotspots by Stage[/bold cyan]")
        top = sorted(reroute.items(), key=lambda x: x[1], reverse=True)
        for stage, cnt in top:
            bar = "█" * min(20, cnt)
            console.print(f"  [yellow]{stage:<30}[/yellow] {bar} {cnt}")

    # Multi-cycle claim IDs
    if mc_ids:
        console.print(f"\n[bold cyan]Multi-Cycle Claims ({len(mc_ids)})[/bold cyan]")
        console.print("[dim]These required >1 pipeline cycle — ideal for manual eval review:[/dim]")
        for cid in mc_ids[:10]:
            console.print(f"  • {cid}")
        if len(mc_ids) > 10:
            console.print(f"  [dim]... and {len(mc_ids) - 10} more[/dim]")


# ------------------------------------------------------------------ #
# Command: web  (NiceGUI web interface)
# ------------------------------------------------------------------ #

@cli.command("web")
@click.option("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
@click.option("--port", default=8888, type=int, help="Port (default: 8888)")
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on code changes (dev)")
def web_cmd(host: str, port: int, reload: bool):
    """
    Launch the NiceGUI web interface.

    Opens a browser-based UI for processing claims, reviewing the queue,
    viewing statistics, and running evaluations. All functionality calls
    the same backend as the CLI — no separate server needed.

    Examples:\n
        rcm-denial web\n
        rcm-denial web --port 3000\n
        rcm-denial web --reload      (dev mode with auto-reload)
    """
    try:
        from rcm_denial.web.app import start
    except ImportError:
        console.print(
            "[red]Error:[/red] NiceGUI is not installed.\n"
            "Install it with:  [cyan]pip install nicegui[/cyan]"
        )
        sys.exit(1)

    console.print(Panel.fit(
        f"[bold blue]RCM Denial Management — Web UI[/bold blue]\n"
        f"[dim]http://{host}:{port}[/dim]",
        border_style="blue",
    ))
    start(host=host, port=port, reload=reload)


# ------------------------------------------------------------------ #
# Command: run-tests
# ------------------------------------------------------------------ #

@cli.command("run-tests")
@click.option("--verbose", "-v", is_flag=True, default=False)
def run_tests_cmd(verbose: bool):
    """Run the test suite."""
    import subprocess
    args = ["python", "-m", "pytest", "tests/", "-v" if verbose else "-q"]
    result = subprocess.run(args, cwd=Path(__file__).parent)
    sys.exit(result.returncode)


# ------------------------------------------------------------------ #
# Programmatic API (for integration into existing applications)
# ------------------------------------------------------------------ #

def process_claim_api(claim: dict) -> dict:
    """
    Public programmatic API for integrating into existing applications.

    Minimal integration footprint — import this function and call it
    with a claim dict. Returns a JSON-serializable dict.

    Example:
        from main import process_claim_api
        result = process_claim_api({
            "claim_id": "CLM-001",
            "patient_id": "PAT001",
            ...
        })
    """
    from rcm_denial.workflows.denial_graph import process_claim
    package = process_claim(claim)
    return package.model_dump()


def process_batch_api(csv_path: str) -> dict:
    """
    Public programmatic API for batch processing.

    Example:
        from main import process_batch_api
        report = process_batch_api("data/sample_denials.csv")
    """
    from rcm_denial.workflows.batch_processor import process_batch
    report = process_batch(csv_path)
    return report.model_dump()


if __name__ == "__main__":
    cli()
