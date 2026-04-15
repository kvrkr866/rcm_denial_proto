##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: test_sop_pipeline_mode.py
# Purpose: Unit tests for SOP RAG pipeline mode toggle,
#          manifest read/write, and check_payer_coverage().
#
#          These tests never touch ChromaDB or the OpenAI
#          embedding API — all external calls are mocked.
#
##########################################################

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ──────────────────────────────────────────────────────────────────────
# Pipeline mode toggle
# ──────────────────────────────────────────────────────────────────────

class TestPipelineMode:
    def test_default_is_off(self):
        import importlib
        import rcm_denial.tools.sop_rag_tool as rag_mod
        importlib.reload(rag_mod)   # reset module state
        assert rag_mod._pipeline_mode is False

    def test_set_pipeline_mode_true(self):
        from rcm_denial.tools.sop_rag_tool import set_pipeline_mode
        import rcm_denial.tools.sop_rag_tool as rag_mod
        set_pipeline_mode(True)
        assert rag_mod._pipeline_mode is True
        set_pipeline_mode(False)    # reset for other tests

    def test_set_pipeline_mode_false(self):
        from rcm_denial.tools.sop_rag_tool import set_pipeline_mode
        import rcm_denial.tools.sop_rag_tool as rag_mod
        set_pipeline_mode(True)
        set_pipeline_mode(False)
        assert rag_mod._pipeline_mode is False

    def test_pipeline_mode_flag_is_global(self):
        """Pipeline mode flag is module-level and affects all callers."""
        import rcm_denial.tools.sop_rag_tool as rag_mod
        from rcm_denial.tools.sop_rag_tool import set_pipeline_mode
        set_pipeline_mode(True)
        assert rag_mod._pipeline_mode is True
        set_pipeline_mode(False)
        assert rag_mod._pipeline_mode is False


# ──────────────────────────────────────────────────────────────────────
# Manifest read / write / upsert
# ──────────────────────────────────────────────────────────────────────

class TestManifest:
    @pytest.fixture(autouse=True)
    def _patch_sop_dir(self, isolated_sop_dir):
        self.sop_dir = isolated_sop_dir

    def test_read_manifest_when_missing_returns_empty(self):
        from rcm_denial.services.sop_ingestion import read_manifest
        manifest = read_manifest()
        assert manifest == {"last_updated": None, "payers": {}}

    def test_write_then_read_manifest(self):
        from rcm_denial.services.sop_ingestion import _write_manifest, read_manifest
        data = {"last_updated": None, "payers": {"bcbs": {"status": "ok", "document_count": 5}}}
        _write_manifest(data)
        result = read_manifest()
        assert "bcbs" in result["payers"]
        assert result["payers"]["bcbs"]["document_count"] == 5
        assert result["last_updated"] is not None   # _write_manifest stamps this

    def test_upsert_manifest_entry_creates_new(self):
        from rcm_denial.services.sop_ingestion import _upsert_manifest_entry, read_manifest
        _upsert_manifest_entry(
            "aetna",
            document_count=8,
            indexed_at="2024-01-01T00:00:00",
            sop_dir_exists=True,
            status="ok",
        )
        m = read_manifest()
        assert "aetna" in m["payers"]
        assert m["payers"]["aetna"]["document_count"] == 8
        assert m["payers"]["aetna"]["collection_name"] == "sop_aetna"

    def test_upsert_manifest_entry_overwrites(self):
        from rcm_denial.services.sop_ingestion import _upsert_manifest_entry, read_manifest
        _upsert_manifest_entry("cigna", document_count=3,
                               indexed_at="2024-01-01T00:00:00",
                               sop_dir_exists=True, status="ok")
        _upsert_manifest_entry("cigna", document_count=10,
                               indexed_at="2024-06-01T00:00:00",
                               sop_dir_exists=True, status="ok",
                               verified_at="2024-06-01T01:00:00",
                               verify_hit_count=4)
        m = read_manifest()
        assert m["payers"]["cigna"]["document_count"] == 10
        assert m["payers"]["cigna"]["verify_hit_count"] == 4

    def test_multiple_payers_coexist(self):
        from rcm_denial.services.sop_ingestion import _upsert_manifest_entry, read_manifest
        for payer in ("bcbs", "aetna", "humana"):
            _upsert_manifest_entry(payer, document_count=5,
                                   indexed_at="2024-01-01T00:00:00",
                                   sop_dir_exists=True, status="ok")
        m = read_manifest()
        assert set(m["payers"].keys()) == {"bcbs", "aetna", "humana"}


# ──────────────────────────────────────────────────────────────────────
# check_payer_coverage
# ──────────────────────────────────────────────────────────────────────

class TestCheckPayerCoverage:
    @pytest.fixture(autouse=True)
    def _use_isolated_sop(self, isolated_sop_dir):
        pass

    def test_all_covered(self):
        from rcm_denial.services.sop_ingestion import (
            _upsert_manifest_entry,
            check_payer_coverage,
        )
        for payer in ("bcbs", "aetna"):
            _upsert_manifest_entry(payer, document_count=5,
                                   indexed_at="2024-01-01T00:00:00",
                                   sop_dir_exists=True, status="ok")
        result = check_payer_coverage(["BCBS", "AETNA"])
        assert result["covered"] == ["bcbs", "aetna"] or set(result["covered"]) == {"bcbs", "aetna"}
        assert result["missing"] == []
        assert result["coverage_pct"] == pytest.approx(100.0)

    def test_partially_covered(self):
        from rcm_denial.services.sop_ingestion import (
            _upsert_manifest_entry,
            check_payer_coverage,
        )
        _upsert_manifest_entry("bcbs", document_count=5,
                               indexed_at="2024-01-01T00:00:00",
                               sop_dir_exists=True, status="ok")
        result = check_payer_coverage(["BCBS", "CIGNA", "HUMANA"])
        assert "bcbs" in result["covered"]
        missing_normalized = {m.lower() for m in result["missing"]}
        assert "cigna" in missing_normalized
        assert "humana" in missing_normalized
        assert result["coverage_pct"] == pytest.approx(100 / 3, abs=1.0)

    def test_none_covered(self):
        from rcm_denial.services.sop_ingestion import check_payer_coverage
        result = check_payer_coverage(["PAYER_A", "PAYER_B"])
        assert result["covered"] == []
        assert len(result["missing"]) == 2
        assert result["coverage_pct"] == pytest.approx(0.0)

    def test_empty_payer_list(self):
        from rcm_denial.services.sop_ingestion import check_payer_coverage
        result = check_payer_coverage([])
        assert result["coverage_pct"] == pytest.approx(100.0)
        assert result["covered"] == []

    def test_degraded_collection_reported(self):
        """Collections with status='empty' appear in degraded, not covered."""
        from rcm_denial.services.sop_ingestion import (
            _upsert_manifest_entry,
            check_payer_coverage,
        )
        _upsert_manifest_entry("medicare", document_count=0,
                               indexed_at="2024-01-01T00:00:00",
                               sop_dir_exists=True, status="empty")
        result = check_payer_coverage(["MEDICARE"])
        assert "medicare" in result.get("degraded", []) or "medicare" in result.get("missing", [])


# ──────────────────────────────────────────────────────────────────────
# normalize_payer_id
# ──────────────────────────────────────────────────────────────────────

class TestNormalizePayerId:
    def test_lowercase(self):
        from rcm_denial.tools.sop_rag_tool import normalize_payer_id
        assert normalize_payer_id("BCBS") == "bcbs"

    def test_spaces_to_underscore(self):
        from rcm_denial.tools.sop_rag_tool import normalize_payer_id
        assert normalize_payer_id("Blue Cross Blue Shield") == "blue_cross_blue_shield"

    def test_strips_whitespace(self):
        from rcm_denial.tools.sop_rag_tool import normalize_payer_id
        assert normalize_payer_id("  aetna  ") == "aetna"

    def test_already_normalized(self):
        from rcm_denial.tools.sop_rag_tool import normalize_payer_id
        assert normalize_payer_id("bcbs") == "bcbs"
