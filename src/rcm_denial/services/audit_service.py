##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: audit_service.py
# Purpose: Configures structlog for structured JSON logging
#          and provides a factory function to get per-module
#          loggers with bound claim context.
#
##########################################################

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

import structlog

from rcm_denial.config.settings import settings


def configure_logging() -> None:
    """
    Sets up structlog with JSON rendering for production and
    colored console output for development. Call once at startup.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Ensure log directory exists
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.log_dir / "rcm_denial.log"

    # Standard library logging config
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.env != "development":
        file_handler = logging.FileHandler(log_file)
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
        force=True,
    )

    # Shared processors for all environments
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.env == "development":
        # Human-readable colored output for local dev
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True)
        ]
    else:
        # JSON lines for log aggregation (Datadog, CloudWatch, etc.)
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, **initial_context: Any) -> structlog.BoundLogger:
    """
    Returns a structlog logger bound with the given context.
    Typical usage:
        logger = get_logger(__name__, claim_id="CLM-001", node="intake_agent")
    """
    return structlog.get_logger(name).bind(**initial_context)


def bind_claim_context(claim_id: str, node_name: str, run_id: str = "") -> None:
    """Binds claim-level context into structlog's contextvars (per-coroutine)."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        claim_id=claim_id,
        node=node_name,
        run_id=run_id,
    )


# Initialize logging immediately on import
configure_logging()
