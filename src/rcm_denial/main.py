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
# Command: seed-kb
# ------------------------------------------------------------------ #

@cli.command("seed-kb")
def seed_kb_cmd():
    """
    Seed the ChromaDB vector store with SOP documents.

    Run this once before first use or after adding new SOP documents.
    Requires OPENAI_API_KEY to be set in .env.
    """
    console.print("[bold blue]Seeding SOP knowledge base...[/bold blue]")

    sys.path.insert(0, str(Path(__file__).parent))
    from scripts.seed_knowledge_base import seed_knowledge_base

    count = seed_knowledge_base()
    if count > 0:
        console.print(f"[green]✓[/green] Indexed {count} SOP documents into ChromaDB")
    else:
        console.print("[red]✗[/red] Seeding failed — check logs for details")
        sys.exit(1)


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
