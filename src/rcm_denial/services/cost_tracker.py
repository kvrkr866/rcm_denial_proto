##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: cost_tracker.py
# Purpose: Gap 49 — LLM cost tracking per call / per claim / per batch.
#
#          Every LLM call emitted by the pipeline can record:
#            model name, input_tokens, output_tokens, cost_usd,
#            agent_name, run_id, batch_id.
#
#          Data is persisted in the llm_cost_log SQLite table and
#          queryable via get_claim_cost() / get_batch_cost_summary().
#
#          LLM call sites call record_llm_call() immediately after
#          receiving a response object. For OpenAI this looks like:
#
#              from rcm_denial.services.cost_tracker import record_llm_call
#              response = client.chat.completions.create(...)
#              record_llm_call(
#                  run_id=state.run_id,
#                  batch_id=state.batch_id,
#                  agent_name="evidence_check_agent",
#                  model=response.model,
#                  input_tokens=response.usage.prompt_tokens,
#                  output_tokens=response.usage.completion_tokens,
#              )
#
##########################################################

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Pricing table  (USD per token, updated as of early 2025)
# Add new models here as they are added to the project.
# ------------------------------------------------------------------ #

PRICING: dict[str, dict[str, float]] = {
    # OpenAI — chat models
    "gpt-4o":                 {"input": 5.00  / 1_000_000, "output": 15.00 / 1_000_000},
    "gpt-4o-2024-05-13":      {"input": 5.00  / 1_000_000, "output": 15.00 / 1_000_000},
    "gpt-4o-2024-08-06":      {"input": 2.50  / 1_000_000, "output": 10.00 / 1_000_000},
    "gpt-4o-mini":            {"input": 0.15  / 1_000_000, "output": 0.60  / 1_000_000},
    "gpt-4o-mini-2024-07-18": {"input": 0.15  / 1_000_000, "output": 0.60  / 1_000_000},
    "gpt-4-turbo":            {"input": 10.00 / 1_000_000, "output": 30.00 / 1_000_000},
    "gpt-3.5-turbo":          {"input": 0.50  / 1_000_000, "output": 1.50  / 1_000_000},
    # OpenAI — embedding models
    "text-embedding-3-small": {"input": 0.02  / 1_000_000, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13  / 1_000_000, "output": 0.0},
    "text-embedding-ada-002": {"input": 0.10  / 1_000_000, "output": 0.0},
}

# Fallback pricing when the model is not in the table
_DEFAULT_PRICING: dict[str, float] = {"input": 5.00 / 1_000_000, "output": 15.00 / 1_000_000}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD for a single LLM call."""
    pricing = PRICING.get(model, _DEFAULT_PRICING)
    return round(
        pricing["input"] * input_tokens + pricing["output"] * output_tokens,
        8,
    )


# ------------------------------------------------------------------ #
# DB helpers
# ------------------------------------------------------------------ #

def _get_db_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def _ensure_table() -> None:
    """Called lazily; _init_db() in claim_intake already creates the table."""
    from rcm_denial.services.claim_intake import _init_db
    _init_db()


# ------------------------------------------------------------------ #
# Public write API
# ------------------------------------------------------------------ #

def record_llm_call(
    *,
    run_id: str,
    batch_id: str,
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
) -> float:
    """
    Persist one LLM call to the llm_cost_log table.

    Returns the cost_usd recorded (useful for callers that want to log it).
    Safe to call in a try/except wrapper — never raises.
    """
    _ensure_table()

    if cost_usd is None:
        cost_usd = calculate_cost(model, input_tokens, output_tokens)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO llm_cost_log
                    (run_id, batch_id, agent_name, model,
                     input_tokens, output_tokens, cost_usd, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, batch_id, agent_name, model,
                    input_tokens, output_tokens, cost_usd,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning(
            "Cost tracking write failed",
            run_id=run_id, agent=agent_name, error=str(exc),
        )

    return cost_usd


# ------------------------------------------------------------------ #
# Public read API
# ------------------------------------------------------------------ #

def get_claim_cost(run_id: str) -> dict:
    """
    Returns total cost and per-agent breakdown for a single claim.

    Example return:
        {
          "run_id": "...",
          "total_cost_usd": 0.000342,
          "total_input_tokens": 1200,
          "total_output_tokens": 450,
          "total_calls": 2,
          "by_agent": {
              "evidence_check_agent": {"calls": 1, "cost_usd": 0.000180, ...},
              "response_agent":       {"calls": 1, "cost_usd": 0.000162, ...},
          }
        }
    """
    _ensure_table()

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT agent_name, model,
                   COUNT(*)           AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd
              FROM llm_cost_log
             WHERE run_id = ?
             GROUP BY agent_name, model
            """,
            (run_id,),
        ).fetchall()

    by_agent: dict = {}
    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_calls = 0

    for r in rows:
        key = r["agent_name"]
        entry = by_agent.setdefault(key, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "models": []})
        entry["calls"]         += r["calls"]
        entry["cost_usd"]      += r["cost_usd"] or 0.0
        entry["input_tokens"]  += r["input_tokens"] or 0
        entry["output_tokens"] += r["output_tokens"] or 0
        if r["model"] not in entry["models"]:
            entry["models"].append(r["model"])
        total_cost   += r["cost_usd"] or 0.0
        total_input  += r["input_tokens"] or 0
        total_output += r["output_tokens"] or 0
        total_calls  += r["calls"]

    return {
        "run_id":               run_id,
        "total_cost_usd":       round(total_cost, 8),
        "total_input_tokens":   total_input,
        "total_output_tokens":  total_output,
        "total_calls":          total_calls,
        "by_agent":             by_agent,
    }


def get_batch_cost_summary(batch_id: str = "") -> dict:
    """
    Aggregated cost metrics for a batch or all-time.

    Returns:
        {
          "batch_id": "...",
          "total_cost_usd": ...,
          "total_calls": ...,
          "claims_tracked": ...,
          "avg_cost_per_claim": ...,
          "by_model": { "gpt-4o": {"calls": N, "cost_usd": X}, ... },
          "by_agent": { "evidence_check_agent": {...}, ... },
        }
    """
    _ensure_table()

    where = "WHERE batch_id = ?" if batch_id else ""
    params: list = [batch_id] if batch_id else []

    with sqlite3.connect(_get_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        by_model = conn.execute(
            f"""
            SELECT model,
                   COUNT(*)           AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd
              FROM llm_cost_log {where}
             GROUP BY model
            """,
            params,
        ).fetchall()

        by_agent = conn.execute(
            f"""
            SELECT agent_name,
                   COUNT(*)           AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd
              FROM llm_cost_log {where}
             GROUP BY agent_name
            """,
            params,
        ).fetchall()

        claims_row = conn.execute(
            f"SELECT COUNT(DISTINCT run_id) AS cnt FROM llm_cost_log {where}",
            params,
        ).fetchone()

    total_cost = sum(r["cost_usd"] or 0.0 for r in by_model)
    claims_tracked = claims_row["cnt"] if claims_row else 0
    avg_per_claim = round(total_cost / claims_tracked, 8) if claims_tracked else 0.0

    return {
        "batch_id":          batch_id or "all_time",
        "total_cost_usd":    round(total_cost, 8),
        "total_calls":       sum(r["calls"] for r in by_model),
        "claims_tracked":    claims_tracked,
        "avg_cost_per_claim": avg_per_claim,
        "by_model": {
            r["model"]: {
                "calls":         r["calls"],
                "input_tokens":  r["input_tokens"] or 0,
                "output_tokens": r["output_tokens"] or 0,
                "cost_usd":      round(r["cost_usd"] or 0.0, 8),
            }
            for r in by_model
        },
        "by_agent": {
            r["agent_name"]: {
                "calls":         r["calls"],
                "input_tokens":  r["input_tokens"] or 0,
                "output_tokens": r["output_tokens"] or 0,
                "cost_usd":      round(r["cost_usd"] or 0.0, 8),
            }
            for r in by_agent
        },
    }
