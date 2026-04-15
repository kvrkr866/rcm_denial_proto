##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: db_backend.py
# Purpose: Gap 48 — Database backend abstraction.
#
#          Default: SQLite (zero-config, works everywhere).
#          Production: PostgreSQL (set DATABASE_URL in .env).
#
#          The module exposes:
#            get_connection()    — returns sqlite3 / psycopg2 connection
#            get_placeholder()   — returns "?" (sqlite) or "%s" (postgres)
#            get_db_type()       — "sqlite" | "postgresql"
#            migrate_sqlite_to_postgres()  — one-time data migration helper
#
#          Switching to PostgreSQL:
#          ─────────────────────────────────────────────────
#          1. Set in .env:
#               DATABASE_URL=postgresql://user:pass@host:5432/rcm_denial
#               DATABASE_TYPE=postgresql
#
#          2. Create schema on the postgres server:
#               psql -U user -d rcm_denial -f data/schema.sql
#             (schema.sql is exported by: rcm-denial db export-schema)
#
#          3. Migrate existing SQLite data (optional):
#               rcm-denial db migrate-to-postgres
#
#          All existing service code continues to work — the SQLite
#          connection factory calls are wrapped by get_connection().
#          New service code should call get_connection() directly.
#          ─────────────────────────────────────────────────
#
##########################################################

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Backend detection
# ------------------------------------------------------------------ #

def get_db_type() -> str:
    """Returns 'postgresql' if DATABASE_URL is configured, else 'sqlite'."""
    from rcm_denial.config.settings import settings
    if settings.database_type == "postgresql" and settings.database_url:
        return "postgresql"
    return "sqlite"


def get_placeholder() -> str:
    """
    Returns the parameter placeholder for the active backend.
    SQLite uses '?', PostgreSQL uses '%s'.

    Usage:
        ph = get_placeholder()
        conn.execute(f"SELECT * FROM t WHERE id = {ph}", (id_val,))
    """
    return "%s" if get_db_type() == "postgresql" else "?"


# ------------------------------------------------------------------ #
# Connection factory
# ------------------------------------------------------------------ #

def _get_sqlite_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.data_dir / "rcm_denial.db"


def get_connection():
    """
    Returns an open database connection for the active backend.

    For SQLite: returns sqlite3.Connection (row_factory NOT set here;
                set it on the returned connection if needed).
    For PostgreSQL: returns psycopg2.Connection with autocommit=False.

    Caller is responsible for closing the connection (or use
    get_db_context() as a context manager instead).

    Example (SQLite — identical to existing code):
        with get_connection() as conn:
            rows = conn.execute("SELECT ...").fetchall()

    Example (PostgreSQL — same pattern):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ...")
        conn.commit()
        conn.close()
    """
    db_type = get_db_type()

    if db_type == "postgresql":
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2 is required for PostgreSQL backend. "
                "Install with: pip install psycopg2-binary"
            ) from exc

        from rcm_denial.config.settings import settings
        conn = psycopg2.connect(settings.database_url)
        conn.autocommit = False
        logger.debug("PostgreSQL connection opened", url=settings.database_url[:30] + "...")
        return conn

    # Default: SQLite
    conn = sqlite3.connect(_get_sqlite_path())
    return conn


@contextmanager
def get_db_context(row_factory: bool = False) -> Generator[Any, None, None]:
    """
    Context manager that opens, yields, and closes a DB connection.
    Commits on clean exit; rolls back on exception.

    Args:
        row_factory: If True and backend is SQLite, sets row_factory=sqlite3.Row
                     so rows behave like dicts.

    Example:
        with get_db_context(row_factory=True) as conn:
            rows = conn.execute("SELECT * FROM submission_log").fetchall()
    """
    conn = get_connection()
    if row_factory and get_db_type() == "sqlite":
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# Schema export (for PostgreSQL migration)
# ------------------------------------------------------------------ #

_POSTGRES_SCHEMA = """
-- RCM Denial Management — PostgreSQL schema
-- Run this on the target PostgreSQL database BEFORE switching DATABASE_TYPE.
-- Generated from SQLite schema with type adjustments.

CREATE TABLE IF NOT EXISTS claim_intake_log (
    id                 SERIAL       PRIMARY KEY,
    batch_id           TEXT,
    source_file        TEXT,
    row_number         INTEGER,
    claim_id           TEXT,
    status             TEXT         NOT NULL,
    rejection_reasons  TEXT,
    raw_data           TEXT,
    recorded_at        TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS claim_audit_log (
    id                 SERIAL       PRIMARY KEY,
    batch_id           TEXT,
    run_id             TEXT,
    claim_id           TEXT         NOT NULL,
    node_name          TEXT         NOT NULL,
    status             TEXT         NOT NULL,
    details            TEXT,
    duration_ms        FLOAT,
    token_usage        TEXT,
    recorded_at        TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS claim_pipeline_result (
    id                 SERIAL       PRIMARY KEY,
    batch_id           TEXT,
    run_id             TEXT,
    claim_id           TEXT         NOT NULL,
    carc_code          TEXT,
    denial_category    TEXT,
    recommended_action TEXT,
    final_status       TEXT,
    package_type       TEXT,
    errors             TEXT,
    pipeline_errors    TEXT,
    duration_ms        FLOAT,
    llm_calls          INTEGER      DEFAULT 0,
    recorded_at        TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS medical_record_source_registry (
    id                 SERIAL       PRIMARY KEY,
    provider_id        TEXT         NOT NULL UNIQUE,
    provider_name      TEXT,
    access_method      TEXT         NOT NULL DEFAULT 'mock',
    endpoint_url       TEXT,
    credentials_ref    TEXT,
    last_verified_at   TIMESTAMPTZ,
    last_verified_ok   INTEGER      DEFAULT 1,
    notes              TEXT,
    registered_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payer_submission_registry (
    id                  SERIAL       PRIMARY KEY,
    payer_id            TEXT         NOT NULL UNIQUE,
    submission_method   TEXT         NOT NULL DEFAULT 'mock',
    api_endpoint        TEXT,
    portal_url          TEXT,
    clearinghouse_id    TEXT,
    notes               TEXT,
    registered_at       TIMESTAMPTZ  DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS submission_log (
    id                    SERIAL       PRIMARY KEY,
    run_id                TEXT         NOT NULL,
    claim_id              TEXT         NOT NULL,
    batch_id              TEXT         NOT NULL DEFAULT '',
    payer_id              TEXT         NOT NULL DEFAULT '',
    submission_method     TEXT         NOT NULL DEFAULT '',
    attempt_number        INTEGER      NOT NULL DEFAULT 1,
    status                TEXT         NOT NULL,
    response_code         TEXT,
    response_message      TEXT,
    confirmation_number   TEXT,
    package_type          TEXT,
    pdf_path              TEXT,
    submitted_at          TIMESTAMPTZ,
    response_received_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS human_review_queue (
    id                    SERIAL       PRIMARY KEY,
    run_id                TEXT         NOT NULL UNIQUE,
    batch_id              TEXT         NOT NULL DEFAULT '',
    claim_id              TEXT         NOT NULL,
    status                TEXT         NOT NULL DEFAULT 'pending',
    payer_id              TEXT,
    carc_code             TEXT,
    billed_amount         FLOAT,
    confidence_score      FLOAT,
    package_type          TEXT,
    output_dir            TEXT,
    pdf_package_path      TEXT,
    is_urgent             INTEGER      DEFAULT 0,
    review_count          INTEGER      DEFAULT 0,
    state_snapshot        TEXT,
    ai_summary            TEXT,
    flag_reasons          TEXT,
    reentry_node          TEXT,
    reviewer_notes        TEXT,
    override_response_text TEXT,
    write_off_reason      TEXT,
    write_off_notes       TEXT,
    reviewer              TEXT,
    reviewed_at           TIMESTAMPTZ,
    enqueued_at           TIMESTAMPTZ  DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS llm_cost_log (
    id              SERIAL       PRIMARY KEY,
    run_id          TEXT         NOT NULL,
    batch_id        TEXT         NOT NULL DEFAULT '',
    agent_name      TEXT         NOT NULL,
    model           TEXT         NOT NULL,
    input_tokens    INTEGER      NOT NULL DEFAULT 0,
    output_tokens   INTEGER      NOT NULL DEFAULT 0,
    cost_usd        FLOAT        NOT NULL DEFAULT 0.0,
    recorded_at     TIMESTAMPTZ  DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_pipeline_batch   ON claim_pipeline_result (batch_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_status  ON claim_pipeline_result (final_status);
CREATE INDEX IF NOT EXISTS idx_submission_run   ON submission_log (run_id);
CREATE INDEX IF NOT EXISTS idx_submission_batch ON submission_log (batch_id);
CREATE INDEX IF NOT EXISTS idx_review_status    ON human_review_queue (status);
CREATE INDEX IF NOT EXISTS idx_review_batch     ON human_review_queue (batch_id);
CREATE INDEX IF NOT EXISTS idx_cost_run         ON llm_cost_log (run_id);
CREATE INDEX IF NOT EXISTS idx_cost_batch       ON llm_cost_log (batch_id);
"""


def export_schema_sql() -> str:
    """Returns the PostgreSQL DDL schema as a string."""
    return _POSTGRES_SCHEMA


def migrate_sqlite_to_postgres() -> dict:
    """
    One-time migration: copies all data from SQLite to the configured
    PostgreSQL database.

    Prerequisites:
    - DATABASE_URL and DATABASE_TYPE=postgresql set in .env
    - PostgreSQL schema already created (run export_schema_sql())
    - psycopg2 installed

    Returns a dict with row counts per table.
    """
    if get_db_type() != "postgresql":
        raise RuntimeError(
            "DATABASE_TYPE must be 'postgresql' and DATABASE_URL must be set "
            "before running migration."
        )

    tables = [
        "claim_intake_log",
        "claim_audit_log",
        "claim_pipeline_result",
        "medical_record_source_registry",
        "payer_submission_registry",
        "submission_log",
        "human_review_queue",
        "llm_cost_log",
    ]

    sqlite_conn = sqlite3.connect(_get_sqlite_path())
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = get_connection()
    pg_cursor = pg_conn.cursor()

    counts: dict[str, int] = {}

    for table in tables:
        rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        if not rows:
            counts[table] = 0
            continue

        cols = rows[0].keys()
        col_list = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))

        data = [tuple(r[c] for c in cols) for r in rows]
        pg_cursor.executemany(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",  # noqa: S608
            data,
        )
        counts[table] = len(data)
        logger.info("Migrated table", table=table, rows=len(data))

    pg_conn.commit()
    pg_conn.close()
    sqlite_conn.close()

    return counts
