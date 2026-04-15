##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: test_cost_tracker.py
# Purpose: Unit tests for Phase 6 LLM cost tracker —
#          calculate_cost(), record_llm_call(),
#          get_claim_cost(), get_batch_cost_summary().
#
##########################################################

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _use_isolated_db(isolated_data_dir):
    yield


# ──────────────────────────────────────────────────────────────────────
# calculate_cost
# ──────────────────────────────────────────────────────────────────────

class TestCalculateCost:
    def test_gpt4o_known_rate(self):
        from rcm_denial.services.cost_tracker import calculate_cost
        # gpt-4o: $5.00/M input, $15.00/M output
        cost = calculate_cost("gpt-4o", input_tokens=1_000_000, output_tokens=0)
        assert cost == pytest.approx(5.00, rel=1e-6)

    def test_gpt4o_mini_output(self):
        from rcm_denial.services.cost_tracker import calculate_cost
        # gpt-4o-mini: $0.60/M output
        cost = calculate_cost("gpt-4o-mini", input_tokens=0, output_tokens=1_000_000)
        assert cost == pytest.approx(0.60, rel=1e-6)

    def test_embedding_model_zero_output(self):
        from rcm_denial.services.cost_tracker import calculate_cost
        # Embedding models have no output cost
        cost = calculate_cost("text-embedding-3-small", input_tokens=500, output_tokens=999)
        input_cost = 0.02 / 1_000_000 * 500
        assert cost == pytest.approx(input_cost, rel=1e-4)

    def test_unknown_model_uses_default(self):
        from rcm_denial.services.cost_tracker import _DEFAULT_PRICING, calculate_cost
        cost = calculate_cost("unknown-model-xyz", input_tokens=100, output_tokens=50)
        expected = _DEFAULT_PRICING["input"] * 100 + _DEFAULT_PRICING["output"] * 50
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_zero_tokens_returns_zero(self):
        from rcm_denial.services.cost_tracker import calculate_cost
        assert calculate_cost("gpt-4o", 0, 0) == 0.0

    def test_small_real_call(self):
        from rcm_denial.services.cost_tracker import calculate_cost
        # ~1000 input + ~400 output with gpt-4o-mini
        cost = calculate_cost("gpt-4o-mini", input_tokens=1000, output_tokens=400)
        assert cost > 0
        assert cost < 0.01  # must be sub-cent for small calls


# ──────────────────────────────────────────────────────────────────────
# record_llm_call
# ──────────────────────────────────────────────────────────────────────

class TestRecordLlmCall:
    def test_records_and_returns_cost(self):
        from rcm_denial.services.cost_tracker import calculate_cost, record_llm_call
        cost = record_llm_call(
            run_id="RUN-001",
            batch_id="BATCH-001",
            agent_name="evidence_check_agent",
            model="gpt-4o-mini",
            input_tokens=800,
            output_tokens=300,
        )
        expected = calculate_cost("gpt-4o-mini", 800, 300)
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_accepts_explicit_cost_usd(self):
        from rcm_denial.services.cost_tracker import record_llm_call
        cost = record_llm_call(
            run_id="RUN-EXPLICIT",
            batch_id="BATCH-001",
            agent_name="response_agent",
            model="gpt-4o",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.99,
        )
        assert cost == pytest.approx(0.99)

    def test_multiple_calls_accumulate(self):
        from rcm_denial.services.cost_tracker import get_claim_cost, record_llm_call
        run_id = "RUN-MULTI"
        record_llm_call(run_id=run_id, batch_id="B1", agent_name="agent_a",
                        model="gpt-4o-mini", input_tokens=100, output_tokens=50)
        record_llm_call(run_id=run_id, batch_id="B1", agent_name="agent_b",
                        model="gpt-4o-mini", input_tokens=200, output_tokens=100)
        result = get_claim_cost(run_id)
        assert result["total_calls"] == 2
        assert result["total_input_tokens"] == 300
        assert result["total_output_tokens"] == 150


# ──────────────────────────────────────────────────────────────────────
# get_claim_cost
# ──────────────────────────────────────────────────────────────────────

class TestGetClaimCost:
    def _seed(self, run_id: str, batch_id: str = "B1"):
        from rcm_denial.services.cost_tracker import record_llm_call
        record_llm_call(run_id=run_id, batch_id=batch_id,
                        agent_name="evidence_check_agent",
                        model="gpt-4o", input_tokens=1000, output_tokens=400)
        record_llm_call(run_id=run_id, batch_id=batch_id,
                        agent_name="response_agent",
                        model="gpt-4o", input_tokens=1500, output_tokens=600)

    def test_returns_correct_structure(self):
        from rcm_denial.services.cost_tracker import get_claim_cost
        self._seed("RUN-STRUCT")
        result = get_claim_cost("RUN-STRUCT")
        assert result["run_id"] == "RUN-STRUCT"
        assert result["total_calls"] == 2
        assert "by_agent" in result
        assert "evidence_check_agent" in result["by_agent"]
        assert "response_agent" in result["by_agent"]

    def test_total_cost_is_sum_of_agents(self):
        from rcm_denial.services.cost_tracker import get_claim_cost
        self._seed("RUN-SUM")
        result = get_claim_cost("RUN-SUM")
        agent_sum = sum(v["cost_usd"] for v in result["by_agent"].values())
        assert result["total_cost_usd"] == pytest.approx(agent_sum, rel=1e-6)

    def test_missing_run_id_returns_empty(self):
        from rcm_denial.services.cost_tracker import get_claim_cost
        result = get_claim_cost("RUN-DOES-NOT-EXIST")
        assert result["total_calls"] == 0
        assert result["total_cost_usd"] == 0.0


# ──────────────────────────────────────────────────────────────────────
# get_batch_cost_summary
# ──────────────────────────────────────────────────────────────────────

class TestGetBatchCostSummary:
    def _seed_batch(self, batch_id: str = "BATCH-COST-001"):
        from rcm_denial.services.cost_tracker import record_llm_call
        for i in range(3):
            run_id = f"RUN-BATCH-{i:02d}"
            record_llm_call(run_id=run_id, batch_id=batch_id,
                            agent_name="evidence_check_agent",
                            model="gpt-4o-mini", input_tokens=500, output_tokens=200)
            record_llm_call(run_id=run_id, batch_id=batch_id,
                            agent_name="response_agent",
                            model="gpt-4o-mini", input_tokens=800, output_tokens=400)

    def test_batch_summary_structure(self):
        from rcm_denial.services.cost_tracker import get_batch_cost_summary
        self._seed_batch()
        summary = get_batch_cost_summary("BATCH-COST-001")
        assert summary["batch_id"] == "BATCH-COST-001"
        assert summary["total_calls"] == 6   # 3 claims × 2 agents
        assert summary["claims_tracked"] == 3
        assert "by_model" in summary
        assert "by_agent" in summary
        assert "gpt-4o-mini" in summary["by_model"]

    def test_avg_cost_per_claim(self):
        from rcm_denial.services.cost_tracker import get_batch_cost_summary
        self._seed_batch("BATCH-AVG-001")
        summary = get_batch_cost_summary("BATCH-AVG-001")
        expected_avg = summary["total_cost_usd"] / summary["claims_tracked"]
        assert summary["avg_cost_per_claim"] == pytest.approx(expected_avg, rel=1e-6)

    def test_all_time_summary_no_filter(self):
        from rcm_denial.services.cost_tracker import get_batch_cost_summary, record_llm_call
        record_llm_call(run_id="RUN-AT-001", batch_id="BATCH-X",
                        agent_name="agent_a", model="gpt-4o-mini",
                        input_tokens=100, output_tokens=50)
        summary = get_batch_cost_summary("")   # empty = all-time
        assert summary["batch_id"] == "all_time"
        assert summary["total_calls"] >= 1
