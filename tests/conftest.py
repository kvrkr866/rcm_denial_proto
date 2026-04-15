##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: conftest.py
# Purpose: Shared pytest fixtures for all test modules.
#
##########################################################

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure src/ is on the path for all tests
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """
    Redirect all SQLite DB access to a per-test temp directory.

    Patches _get_db_path() in every service module that accesses the DB,
    bypassing all settings resolution issues.  Then creates all required
    tables in the temp DB.
    """
    db_path = tmp_path / "rcm_denial.db"
    _get_db = lambda: db_path  # noqa: E731

    # Patch every module that has _get_db_path
    for mod_path in (
        "rcm_denial.services.claim_intake",
        "rcm_denial.services.review_queue",
        "rcm_denial.services.cost_tracker",
        "rcm_denial.services.submission_service",
        "rcm_denial.services.submission_adapters",
        "rcm_denial.services.checkpoint_service",
    ):
        try:
            monkeypatch.setattr(f"{mod_path}._get_db_path", _get_db)
        except AttributeError:
            pass  # module may not have _get_db_path

    # Create all tables — _init_db only creates core 4 tables;
    # other tables are lazily created in various service functions.
    from rcm_denial.services.claim_intake import _init_db
    _init_db()

    # Create remaining tables that are normally lazily created
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS human_review_queue (
            id                      INTEGER  PRIMARY KEY AUTOINCREMENT,
            run_id                  TEXT     NOT NULL UNIQUE,
            batch_id                TEXT,
            claim_id                TEXT     NOT NULL,
            status                  TEXT     NOT NULL DEFAULT 'pending',
            package_type            TEXT,
            billed_amount           REAL,
            carc_code               TEXT,
            denial_category         TEXT,
            routing_decision        TEXT,
            confidence_score        REAL,
            evidence_confidence     REAL,
            prior_appeal_attempts   INTEGER  DEFAULT 0,
            review_count            INTEGER  DEFAULT 0,
            ai_summary              TEXT,
            reviewer_notes          TEXT,
            reentry_node            TEXT,
            override_response_text  TEXT,
            write_off_reason        TEXT,
            write_off_notes         TEXT,
            reviewed_by             TEXT,
            reviewed_at             TIMESTAMP,
            state_snapshot          TEXT,
            output_dir              TEXT,
            pdf_package_path        TEXT,
            days_to_appeal_deadline INTEGER,
            is_urgent               INTEGER  DEFAULT 0,
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payer_submission_registry (
            id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
            payer_id            TEXT     NOT NULL UNIQUE,
            payer_name          TEXT,
            submission_method   TEXT     NOT NULL DEFAULT 'mock',
            api_endpoint        TEXT,
            credentials_ref     TEXT,
            portal_url          TEXT,
            clearinghouse_id    TEXT,
            is_active           INTEGER  DEFAULT 1,
            notes               TEXT,
            registered_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submission_log (
            id                    INTEGER  PRIMARY KEY AUTOINCREMENT,
            run_id                TEXT     NOT NULL,
            claim_id              TEXT     NOT NULL,
            batch_id              TEXT,
            payer_id              TEXT,
            submission_method     TEXT,
            attempt_number        INTEGER  DEFAULT 1,
            status                TEXT     NOT NULL,
            response_code         TEXT,
            response_message      TEXT,
            confirmation_number   TEXT,
            package_type          TEXT,
            pdf_path              TEXT,
            submitted_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            response_received_at  TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_cost_log (
            id              INTEGER  PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT     NOT NULL,
            batch_id        TEXT     NOT NULL DEFAULT '',
            agent_name      TEXT     NOT NULL,
            model           TEXT     NOT NULL,
            input_tokens    INTEGER  NOT NULL DEFAULT 0,
            output_tokens   INTEGER  NOT NULL DEFAULT 0,
            cost_usd        REAL     NOT NULL DEFAULT 0.0,
            recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS claim_checkpoint (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id            TEXT,
            run_id              TEXT    NOT NULL,
            claim_id            TEXT    NOT NULL,
            last_completed_node TEXT    NOT NULL,
            node_index          INTEGER NOT NULL,
            state_snapshot      TEXT    NOT NULL,
            status              TEXT    DEFAULT 'in_progress',
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(run_id, claim_id)
        )
    """)
    conn.commit()
    conn.close()

    yield tmp_path


@pytest.fixture
def isolated_sop_dir(tmp_path, monkeypatch):
    """
    Redirect SOP manifest access to a per-test temp directory.
    """
    monkeypatch.setattr(
        "rcm_denial.services.sop_ingestion._manifest_path",
        lambda: tmp_path / "manifest.json",
    )
    yield tmp_path
