##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: rate_limiter.py
# Purpose: Thread-safe token-bucket rate limiter for LLM
#          API calls.  Prevents 429 (Too Many Requests)
#          errors during large batch runs.
#
#          Usage:
#            from rcm_denial.services.rate_limiter import acquire
#            acquire()          # blocks until a token is available
#            result = llm.invoke(prompt)
#
#          Configuration via .env:
#            LLM_REQUESTS_PER_MINUTE=30
#            LLM_BURST_SIZE=5
#
##########################################################

from __future__ import annotations

import threading
import time

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class TokenBucket:
    """
    Simple token-bucket rate limiter.

    Tokens refill at `rate` tokens/second up to `burst_size`.
    `acquire()` blocks (sleeps) until a token is available.
    Thread-safe via threading.Lock.
    """

    def __init__(self, requests_per_minute: int = 30, burst_size: int = 5):
        self.rate: float = requests_per_minute / 60.0   # tokens per second
        self.burst_size: int = burst_size
        self._tokens: float = float(burst_size)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.burst_size,
                    self._tokens + elapsed * self.rate,
                )
                self._last_refill = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

                # Calculate wait time for next token
                wait_time = (1.0 - self._tokens) / self.rate

            logger.debug("Rate limiter throttling", wait_seconds=round(wait_time, 2))
            time.sleep(wait_time)


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton — lazy init from settings
# ──────────────────────────────────────────────────────────────────────

_bucket: TokenBucket | None = None
_init_lock = threading.Lock()


def _get_bucket() -> TokenBucket:
    global _bucket
    if _bucket is None:
        with _init_lock:
            if _bucket is None:
                from rcm_denial.config.settings import settings
                _bucket = TokenBucket(
                    requests_per_minute=settings.llm_requests_per_minute,
                    burst_size=settings.llm_burst_size,
                )
                logger.info(
                    "Rate limiter initialized",
                    rpm=settings.llm_requests_per_minute,
                    burst=settings.llm_burst_size,
                )
    return _bucket


def acquire() -> None:
    """
    Public API: block until an LLM call token is available.

    Call this before every LLM invocation:
        from rcm_denial.services.rate_limiter import acquire
        acquire()
        result = llm.invoke(prompt)
    """
    _get_bucket().acquire()


def reset(requests_per_minute: int | None = None, burst_size: int | None = None) -> None:
    """Reset the global bucket (useful for testing)."""
    global _bucket
    with _init_lock:
        if requests_per_minute is not None and burst_size is not None:
            _bucket = TokenBucket(requests_per_minute=requests_per_minute, burst_size=burst_size)
        else:
            _bucket = None
