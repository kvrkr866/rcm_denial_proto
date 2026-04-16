##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: metrics_service.py
# Purpose: Gap 44 — Prometheus metrics export for the RCM pipeline.
#
#          Designed for a batch CLI tool (not a long-running server),
#          so metrics are collected from SQLite on demand and written
#          to a textfile (data/metrics/rcm_denial.prom).
#
#          Prometheus scrapes the file via node_exporter's
#          textfile collector:
#              --collector.textfile.directory=data/metrics/
#
#          Optionally, metrics can be pushed to a Prometheus
#          Pushgateway (set PROMETHEUS_PUSHGATEWAY_URL in .env).
#
#          Entry points:
#            collect_and_export()     — collect from DB, write file
#            push_to_gateway(url)     — push to Pushgateway (optional)
#            get_current_metrics()    — returns raw metric dict (for CLI)
#
##########################################################

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def _get_metrics_dir() -> Path:
    from rcm_denial.config.settings import settings
    metrics_dir = settings.data_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return metrics_dir


# ------------------------------------------------------------------ #
# Data collection from SQLite
# ------------------------------------------------------------------ #

def get_current_metrics(batch_id: str = "") -> dict:
    """
    Queries all relevant tables and returns a structured metrics dict.
    batch_id="" → all-time aggregates.
    """
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    db = _get_db_path()
    batch_filter = "WHERE batch_id = ?" if batch_id else ""
    params: list = [batch_id] if batch_id else []

    pipeline_rows = []
    payer_rows = []
    queue_rows = []
    submission_rows = []
    writeoff_rows = []
    cost_rows = []
    durations = []

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row

        # ---- Pipeline results ----
        try:
            pipeline_rows = conn.execute(
                f"""
                SELECT final_status, package_type,
                       COUNT(*)          AS cnt,
                       AVG(duration_ms)  AS avg_ms,
                       SUM(llm_calls)    AS total_llm_calls
                  FROM claim_pipeline_result {batch_filter}
                 GROUP BY final_status, package_type
                """,
                params,
            ).fetchall()
        except Exception as exc:
            logger.warning("Metrics: pipeline query failed", error=str(exc))

        # ---- Per-payer claim counts ----
        try:
            payer_rows = conn.execute(
                """
                SELECT carc_code AS payer_id, status AS final_status, COUNT(*) AS cnt
                  FROM human_review_queue
                 WHERE (batch_id = ? OR ? = '')
                 GROUP BY carc_code, status
                """,
                [batch_id, batch_id],
            ).fetchall()
        except Exception:
            pass

        # ---- Review queue depth ----
        try:
            queue_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS cnt
                  FROM human_review_queue
                 WHERE (batch_id = ? OR ? = '')
                 GROUP BY status
                """,
                [batch_id, batch_id],
            ).fetchall()
        except Exception as exc:
            logger.warning("Metrics: review queue query failed", error=str(exc))

        # ---- Submission stats ----
        try:
            submission_rows = conn.execute(
                f"""
                SELECT status, submission_method,
                       COUNT(*) AS cnt
                  FROM submission_log {batch_filter}
                 GROUP BY status, submission_method
                """,
                params,
            ).fetchall()
        except Exception as exc:
            logger.warning("Metrics: submission query failed", error=str(exc))

        # ---- Write-off stats ----
        try:
            writeoff_rows = conn.execute(
                """
                SELECT write_off_reason, COUNT(*) AS cnt,
                       SUM(billed_amount) AS total_amount
                  FROM human_review_queue
                 WHERE status = 'written_off'
                   AND (batch_id = ? OR ? = '')
                 GROUP BY write_off_reason
                """,
                [batch_id, batch_id],
            ).fetchall()
        except Exception as exc:
            logger.warning("Metrics: write-off query failed", error=str(exc))

        # ---- LLM cost ----
        try:
            cost_rows = conn.execute(
                f"""
                SELECT model,
                       SUM(input_tokens)  AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cost_usd)      AS cost_usd,
                       COUNT(*)           AS calls
                  FROM llm_cost_log {batch_filter}
                 GROUP BY model
                """,
                params,
            ).fetchall()
        except Exception as exc:
            logger.warning("Metrics: LLM cost query failed", error=str(exc))

        # ---- Duration percentiles (approximate via SQLite) ----
        try:
            durations = conn.execute(
                f"SELECT duration_ms FROM claim_pipeline_result {batch_filter} ORDER BY duration_ms",
                params,
            ).fetchall()
        except Exception as exc:
            logger.warning("Metrics: duration query failed", error=str(exc))

    # ---- Build structured dict ----
    metrics: dict = {
        "collected_at": datetime.utcnow().isoformat(),
        "batch_id":     batch_id or "all_time",
        "pipeline":     {},
        "payer":        {},
        "review_queue": {},
        "submissions":  {},
        "write_offs":   {"total_count": 0, "total_amount_usd": 0.0, "by_reason": {}},
        "llm_cost":     {"total_cost_usd": 0.0, "total_calls": 0, "by_model": {}},
        "duration_ms":  {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0},
    }

    for r in pipeline_rows:
        key = f"{r['final_status']}_{r['package_type']}"
        metrics["pipeline"][key] = {
            "count":          r["cnt"],
            "avg_duration_ms": round(r["avg_ms"] or 0, 2),
            "total_llm_calls": r["total_llm_calls"] or 0,
        }

    for r in payer_rows:
        payer = r["payer_id"] or "unknown"
        metrics["payer"].setdefault(payer, {})[r["final_status"]] = r["cnt"]

    for r in queue_rows:
        metrics["review_queue"][r["status"]] = r["cnt"]

    for r in submission_rows:
        key = f"{r['status']}_{r['submission_method'] or 'unknown'}"
        metrics["submissions"][key] = r["cnt"]

    for r in writeoff_rows:
        reason = r["write_off_reason"] or "unknown"
        cnt = r["cnt"]
        amt = r["total_amount"] or 0.0
        metrics["write_offs"]["by_reason"][reason] = {"count": cnt, "amount_usd": amt}
        metrics["write_offs"]["total_count"]      += cnt
        metrics["write_offs"]["total_amount_usd"] += amt

    for r in cost_rows:
        model = r["model"]
        cost = r["cost_usd"] or 0.0
        metrics["llm_cost"]["by_model"][model] = {
            "calls":         r["calls"],
            "input_tokens":  r["input_tokens"] or 0,
            "output_tokens": r["output_tokens"] or 0,
            "cost_usd":      round(cost, 8),
        }
        metrics["llm_cost"]["total_cost_usd"] += cost
        metrics["llm_cost"]["total_calls"]    += r["calls"]
    metrics["llm_cost"]["total_cost_usd"] = round(metrics["llm_cost"]["total_cost_usd"], 8)

    if durations:
        vals = [d[0] or 0.0 for d in durations]
        n = len(vals)
        metrics["duration_ms"]["p50"]  = round(vals[int(n * 0.50)], 2)
        metrics["duration_ms"]["p95"]  = round(vals[min(int(n * 0.95), n - 1)], 2)
        metrics["duration_ms"]["p99"]  = round(vals[min(int(n * 0.99), n - 1)], 2)
        metrics["duration_ms"]["avg"]  = round(sum(vals) / n, 2)

    return metrics


# ------------------------------------------------------------------ #
# Prometheus text format writer
# ------------------------------------------------------------------ #

def _prom_counter(name: str, labels: dict, value: float, help_text: str = "") -> list[str]:
    """Render one Prometheus counter line in exposition format."""
    lines = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
    lines.append(f"{name}{{{label_str}}} {value}")
    return lines


def _prom_gauge(name: str, labels: dict, value: float, help_text: str = "") -> list[str]:
    lines = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
    lines.append(f"{name}{{{label_str}}} {value}")
    return lines


def collect_and_export(batch_id: str = "") -> Path:
    """
    Collect metrics from SQLite and write Prometheus textfile.

    The file is written to data/metrics/rcm_denial.prom.
    Configure node_exporter to scrape that directory, OR run
    a Prometheus Pushgateway and call push_to_gateway() after this.

    Returns the path of the written file.
    """
    m = get_current_metrics(batch_id=batch_id)
    lines: list[str] = [
        f"# Generated by rcm-denial metrics_service at {m['collected_at']}",
        f"# Batch: {m['batch_id']}",
        "",
    ]

    # ---- Claims processed ----
    lines.append("# HELP rcm_claims_processed_total Total denied claims processed by status and package type")
    lines.append("# TYPE rcm_claims_processed_total counter")
    for key, data in m["pipeline"].items():
        parts = key.split("_", 1)
        status = parts[0]
        pkg    = parts[1] if len(parts) > 1 else "unknown"
        lines.append(
            f'rcm_claims_processed_total{{status="{status}",package_type="{pkg}"}} {data["count"]}'
        )

    lines.append("")
    lines.append("# HELP rcm_claim_duration_ms_p50 Median claim processing duration in milliseconds")
    lines.append("# TYPE rcm_claim_duration_ms_p50 gauge")
    lines.append(f'rcm_claim_duration_ms_p50{{batch="{m["batch_id"]}"}} {m["duration_ms"]["p50"]}')
    lines.append("# HELP rcm_claim_duration_ms_p95 95th percentile claim processing duration")
    lines.append("# TYPE rcm_claim_duration_ms_p95 gauge")
    lines.append(f'rcm_claim_duration_ms_p95{{batch="{m["batch_id"]}"}} {m["duration_ms"]["p95"]}')
    lines.append("# HELP rcm_claim_duration_ms_p99 99th percentile claim processing duration")
    lines.append("# TYPE rcm_claim_duration_ms_p99 gauge")
    lines.append(f'rcm_claim_duration_ms_p99{{batch="{m["batch_id"]}"}} {m["duration_ms"]["p99"]}')

    # ---- LLM cost (both metric names for compatibility) ----
    lines.append("")
    lines.append("# HELP rcm_llm_cost_usd LLM API cost in USD by model")
    lines.append("# TYPE rcm_llm_cost_usd gauge")
    for model, data in m["llm_cost"]["by_model"].items():
        lines.append(f'rcm_llm_cost_usd{{model="{model}"}} {data["cost_usd"]}')

    lines.append("# HELP rcm_llm_calls_total Total LLM API calls by model")
    lines.append("# TYPE rcm_llm_calls_total gauge")
    for model, data in m["llm_cost"]["by_model"].items():
        lines.append(f'rcm_llm_calls_total{{model="{model}"}} {data["calls"]}')

    # ---- Submission ----
    lines.append("")
    lines.append("# HELP rcm_submission_attempts_total Submission attempts by status and method")
    lines.append("# TYPE rcm_submission_attempts_total counter")
    for key, cnt in m["submissions"].items():
        parts  = key.split("_", 1)
        status = parts[0]
        method = parts[1] if len(parts) > 1 else "unknown"
        lines.append(f'rcm_submission_attempts_total{{status="{status}",method="{method}"}} {cnt}')

    # ---- Review queue ----
    lines.append("")
    lines.append("# HELP rcm_review_queue_depth Current number of claims per review status")
    lines.append("# TYPE rcm_review_queue_depth gauge")
    for status, cnt in m["review_queue"].items():
        lines.append(f'rcm_review_queue_depth{{status="{status}"}} {cnt}')

    # ---- Write-offs ----
    lines.append("")
    lines.append("# HELP rcm_write_offs_total Total write-offs by reason (target: 0)")
    lines.append("# TYPE rcm_write_offs_total counter")
    for reason, data in m["write_offs"]["by_reason"].items():
        lines.append(f'rcm_write_offs_total{{reason="{reason}"}} {data["count"]}')

    # Ensure rcm_write_offs_total exists even if no write-offs
    if not m["write_offs"]["by_reason"]:
        lines.append(f'rcm_write_offs_total{{reason="none"}} 0')

    lines.append("# HELP rcm_write_off_revenue_usd_total Total revenue written off in USD")
    lines.append("# TYPE rcm_write_off_revenue_usd_total gauge")
    lines.append(
        f'rcm_write_off_revenue_usd_total{{batch="{m["batch_id"]}"}} '
        f'{m["write_offs"]["total_amount_usd"]}'
    )

    # ---- First-pass approval rate ----
    lines.append("")
    lines.append("# HELP rcm_first_pass_approval_rate First-pass approval rate (0-1)")
    lines.append("# TYPE rcm_first_pass_approval_rate gauge")
    try:
        from rcm_denial.services.review_queue import get_review_stats
        rq = get_review_stats(batch_id=batch_id)
        fp = rq.get("first_pass_approval_rate_pct")
        if fp is not None:
            lines.append(f'rcm_first_pass_approval_rate {fp / 100.0}')
        else:
            lines.append(f'rcm_first_pass_approval_rate 0')
    except Exception:
        lines.append(f'rcm_first_pass_approval_rate 0')

    prom_path = _get_metrics_dir() / "rcm_denial.prom"
    prom_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info("Prometheus metrics written", path=str(prom_path), batch_id=batch_id or "all_time")
    return prom_path


def push_to_gateway(gateway_url: str, batch_id: str = "", job: str = "rcm_denial") -> None:
    """
    Push ALL current metrics to a Prometheus Pushgateway.

    Requires `prometheus_client` to be installed:
        pip install prometheus_client

    Args:
        gateway_url: e.g. "http://pushgateway:9091"
        batch_id:    Label grouping key for the push
        job:         Prometheus job name (default: rcm_denial)
    """
    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway as _push
    except ImportError:
        logger.warning(
            "prometheus_client not installed — skipping push. "
            "Install with: pip install prometheus_client"
        )
        return

    m = get_current_metrics(batch_id=batch_id)
    registry = CollectorRegistry()

    # ── Claims processed (by status + package_type) ──────────
    claims = Gauge(
        "rcm_claims_processed_total",
        "Total denied claims processed",
        ["status", "package_type"],
        registry=registry,
    )
    for key, data in m["pipeline"].items():
        parts = key.split("_", 1)
        status = parts[0]
        pkg = parts[1] if len(parts) > 1 else "unknown"
        claims.labels(status=status, package_type=pkg).set(data["count"])

    # ── Duration percentiles ─────────────────────────────────
    for pctl in ("p50", "p95", "p99"):
        g = Gauge(
            f"rcm_claim_duration_ms_{pctl}",
            f"{pctl} claim processing duration (ms)",
            registry=registry,
        )
        g.set(m["duration_ms"].get(pctl, 0))

    # ── LLM cost by model ────────────────────────────────────
    llm_cost = Gauge(
        "rcm_llm_cost_usd",
        "LLM API cost in USD",
        ["model"],
        registry=registry,
    )
    llm_calls = Gauge(
        "rcm_llm_calls_total",
        "Total LLM API calls",
        ["model"],
        registry=registry,
    )
    for model, data in m["llm_cost"]["by_model"].items():
        llm_cost.labels(model=model).set(data["cost_usd"])
        llm_calls.labels(model=model).set(data["calls"])

    # ── Review queue depth ───────────────────────────────────
    queue = Gauge(
        "rcm_review_queue_depth",
        "Claims in review queue by status",
        ["status"],
        registry=registry,
    )
    for status, cnt in m["review_queue"].items():
        queue.labels(status=status).set(cnt)

    # ── Submissions ──────────────────────────────────────────
    subs = Gauge(
        "rcm_submission_attempts_total",
        "Submission attempts by status and method",
        ["status", "method"],
        registry=registry,
    )
    for key, cnt in m["submissions"].items():
        parts = key.split("_", 1)
        status = parts[0]
        method = parts[1] if len(parts) > 1 else "unknown"
        subs.labels(status=status, method=method).set(cnt)

    # ── Write-offs ───────────────────────────────────────────
    wo = Gauge(
        "rcm_write_offs_total",
        "Write-offs by reason",
        ["reason"],
        registry=registry,
    )
    for reason, data in m["write_offs"]["by_reason"].items():
        wo.labels(reason=reason).set(data["count"])

    wo_rev = Gauge(
        "rcm_write_off_revenue_usd_total",
        "Revenue written off (USD)",
        registry=registry,
    )
    wo_rev.set(m["write_offs"]["total_amount_usd"])

    # ── First-pass approval rate ─────────────────────────────
    try:
        from rcm_denial.services.review_queue import get_review_stats
        rq = get_review_stats(batch_id=batch_id)
        fp_rate = rq.get("first_pass_approval_rate_pct")
        if fp_rate is not None:
            fp = Gauge(
                "rcm_first_pass_approval_rate",
                "First-pass approval rate (0-1)",
                registry=registry,
            )
            fp.set(fp_rate / 100.0)
    except Exception:
        pass

    # ── Push ─────────────────────────────────────────────────
    grouping_key = {"batch_id": batch_id or "all_time"}
    _push(job, registry=registry, gateway=gateway_url, grouping_key=grouping_key)
    logger.info("All metrics pushed to Pushgateway", gateway=gateway_url, job=job)
