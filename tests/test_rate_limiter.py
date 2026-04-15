##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: test_rate_limiter.py
# Purpose: Unit tests for the token-bucket LLM rate limiter.
#
##########################################################

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rcm_denial.services.rate_limiter import TokenBucket, reset


class TestTokenBucket:
    def test_burst_allows_immediate_calls(self):
        bucket = TokenBucket(requests_per_minute=60, burst_size=5)
        start = time.monotonic()
        for _ in range(5):
            bucket.acquire()
        elapsed = time.monotonic() - start
        # 5 burst calls should be nearly instant (< 0.5s)
        assert elapsed < 0.5

    def test_throttles_after_burst(self):
        bucket = TokenBucket(requests_per_minute=120, burst_size=2)
        # Exhaust burst
        bucket.acquire()
        bucket.acquire()
        # Next call should wait ~0.5s (120 rpm = 2/s, so 1 token refills in 0.5s)
        start = time.monotonic()
        bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3  # some tolerance

    def test_rate_limits_sustained_throughput(self):
        # 60 rpm = 1 per second, burst 1
        bucket = TokenBucket(requests_per_minute=60, burst_size=1)
        bucket.acquire()  # use the burst token
        start = time.monotonic()
        bucket.acquire()  # should wait ~1s
        bucket.acquire()  # should wait ~1s more
        elapsed = time.monotonic() - start
        assert elapsed >= 1.5  # 2 calls at 1/sec should take ~2s, allow some slack

    def test_zero_wait_when_tokens_refilled(self):
        bucket = TokenBucket(requests_per_minute=600, burst_size=3)
        # Exhaust burst
        for _ in range(3):
            bucket.acquire()
        # Wait for refill (600 rpm = 10/s, so 0.1s per token)
        time.sleep(0.5)
        start = time.monotonic()
        bucket.acquire()  # should be instant now
        elapsed = time.monotonic() - start
        assert elapsed < 0.2


class TestModuleLevelAPI:
    def test_acquire_works(self):
        from rcm_denial.services.rate_limiter import acquire
        reset(requests_per_minute=600, burst_size=10)
        acquire()  # should not raise or hang

    def test_reset_clears_state(self):
        from rcm_denial.services.rate_limiter import acquire
        reset(requests_per_minute=600, burst_size=10)
        acquire()
        reset()  # clear
        reset(requests_per_minute=600, burst_size=10)
        acquire()  # should work with fresh bucket
