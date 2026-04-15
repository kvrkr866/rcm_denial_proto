##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: settings.py
# Purpose: Centralized pydantic-settings configuration for all
#          environment variables, paths, and runtime parameters.
#
##########################################################

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Runtime environment
    # ------------------------------------------------------------------ #
    env: Literal["development", "staging", "production"] = "development"

    # ------------------------------------------------------------------ #
    # OpenAI
    # ------------------------------------------------------------------ #
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o", description="LLM model name")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", description="Embedding model"
    )
    openai_max_tokens: int = Field(default=4096)
    openai_temperature: float = Field(default=0.1, ge=0.0, le=2.0)

    # ------------------------------------------------------------------ #
    # LangSmith tracing (optional)
    # ------------------------------------------------------------------ #
    langchain_tracing_v2: bool = Field(default=False)
    langchain_api_key: str = Field(default="")
    langchain_project: str = Field(default="rcm-denial-management")

    # ------------------------------------------------------------------ #
    # ChromaDB / Vector store — per-payer SOP RAG
    # ------------------------------------------------------------------ #
    chroma_persist_dir: Path = Field(default=Path("./data/chroma_db"))
    # Global / fallback collection used when no payer-specific collection exists.
    # Per-payer collections are named automatically: sop_{normalized_payer_id}
    chroma_collection_name: str = Field(default="sop_global")
    # Gap 6 — Freshness check: re-index a payer's collection if its SOP files
    # are newer than the collection's last-indexed timestamp.
    sop_rag_freshness_check: bool = Field(
        default=True,
        description="Re-index stale payer collections when SOP files change",
    )
    # Minimum relevance score for RAG results (0.0–1.0).
    # Hits below this threshold are filtered out before returning.
    sop_min_relevance_score: float = Field(default=0.3, ge=0.0, le=1.0)
    # Pre-flight proposal — pipeline-mode enforcement:
    # False (default): missing collections fall back to keyword search silently.
    # True: process-batch logs a WARNING at startup for every payer without a
    #       collection; operator must run 'rcm-denial init' before the batch.
    #       Does NOT block the batch — claims still run with keyword fallback.
    sop_pipeline_strict_mode: bool = Field(
        default=False,
        description="Warn loudly when a batch payer has no SOP collection (run 'rcm-denial init' first)",
    )

    # ------------------------------------------------------------------ #
    # OCR
    # ------------------------------------------------------------------ #
    tesseract_cmd: str = Field(default="/usr/bin/tesseract")
    ocr_dpi: int = Field(default=300)

    # ------------------------------------------------------------------ #
    # External data source adapters
    # Selects which adapter implementation is active per source family.
    # "mock" uses CSV data + generic placeholders (default for dev/testing).
    # Set to a real adapter name (e.g. "epic", "availity") when integrating
    # with live systems — implement the corresponding subclass first.
    # ------------------------------------------------------------------ #
    emr_adapter: str = Field(
        default="mock",
        description="EMR/EHR adapter: 'mock' | 'epic' | 'cerner' | 'athena'",
    )
    pms_adapter: str = Field(
        default="mock",
        description="PMS adapter: 'mock' | 'kareo' | 'advancedmd'",
    )
    payer_adapter: str = Field(
        default="mock",
        description="Payer adapter: 'mock' | 'availity' | 'change_healthcare'",
    )
    # Gap 20 — submission adapter used when no payer-specific method is registered.
    # Per-payer methods are registered in payer_submission_registry table and take precedence.
    submission_adapter: str = Field(
        default="mock",
        description="Default submission adapter: 'mock' | 'availity_api' | 'rpa_portal' | 'edi_837'",
    )
    # Gap 26 — retry configuration for submission failures
    submission_max_retries: int = Field(default=3, ge=1, le=10)
    submission_retry_delay_seconds: float = Field(default=5.0, ge=1.0)

    # ------------------------------------------------------------------ #
    # Database backend (Gap 48)
    # Default: SQLite (zero config). Switch to PostgreSQL for production.
    # Set DATABASE_URL=postgresql://user:pass@host:5432/rcm_denial in .env
    # ------------------------------------------------------------------ #
    database_type: Literal["sqlite", "postgresql"] = Field(
        default="sqlite",
        description="Database backend: 'sqlite' (default) | 'postgresql'",
    )
    database_url: str = Field(
        default="",
        description="PostgreSQL connection URL (only used when database_type=postgresql)",
    )

    # ------------------------------------------------------------------ #
    # Observability / Prometheus (Gap 44)
    # ------------------------------------------------------------------ #
    prometheus_pushgateway_url: str = Field(
        default="",
        description="Prometheus Pushgateway URL — empty disables push (e.g. http://localhost:9091)",
    )
    metrics_export_after_batch: bool = Field(
        default=True,
        description="Auto-write data/metrics/rcm_denial.prom after each batch run",
    )

    # ------------------------------------------------------------------ #
    # LLM rate limiting
    # ------------------------------------------------------------------ #
    llm_requests_per_minute: int = Field(
        default=30, ge=1,
        description="Max LLM API calls per minute (token bucket rate)",
    )
    llm_burst_size: int = Field(
        default=5, ge=1,
        description="Max burst of rapid LLM calls before throttling kicks in",
    )

    # ------------------------------------------------------------------ #
    # OCR — PyMuPDF + Tesseract
    # ------------------------------------------------------------------ #
    ocr_pymupdf_min_chars: int = Field(
        default=50,
        description="Min chars from PyMuPDF text extraction before falling back to Tesseract OCR",
    )

    # ------------------------------------------------------------------ #
    # Checkpointing (error recovery)
    # ------------------------------------------------------------------ #
    enable_checkpointing: bool = Field(
        default=True,
        description="Save per-node state checkpoints for crash recovery in batch runs",
    )

    # ------------------------------------------------------------------ #
    # Web UI authentication
    # ------------------------------------------------------------------ #
    web_auth_enabled: bool = Field(
        default=False,
        description="Enable login for web UI (set True for any non-local deployment)",
    )
    web_auth_secret: str = Field(
        default="change-me-in-production",
        description="Secret key for NiceGUI session storage (change in production!)",
    )
    web_auth_users: str = Field(
        default="admin:admin",
        description="Comma-separated user:password pairs (e.g. 'admin:admin,reviewer:pass123')",
    )

    # ------------------------------------------------------------------ #
    # Batch processing
    # ------------------------------------------------------------------ #
    batch_max_retries: int = Field(default=3, ge=1, le=10)
    batch_retry_delay_seconds: float = Field(default=2.0, ge=0.5)
    max_concurrent_claims: int = Field(default=1, ge=1)
    skip_completed_claims: bool = Field(default=True)

    # ------------------------------------------------------------------ #
    # Paths
    # ------------------------------------------------------------------ #
    output_dir: Path = Field(default=Path("./output"))
    log_dir: Path = Field(default=Path("./logs"))
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    data_dir: Path = Field(default=Path("./data"))

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #
    @property
    def carc_rarc_reference_path(self) -> Path:
        return self.data_dir / "carc_rarc_reference.json"

    @property
    def sop_documents_dir(self) -> Path:
        """Root dir for SOP documents. Sub-dirs are per-payer: sop_documents/{PAYER_ID}/"""
        return self.data_dir / "sop_documents"

    def sop_payer_dir(self, payer_id: str) -> Path:
        """Returns the SOP documents directory for a specific (normalized) payer ID."""
        from rcm_denial.tools.sop_rag_tool import normalize_payer_id  # lazy import avoids circular
        normalized = normalize_payer_id(payer_id)
        return self.sop_documents_dir / normalized

    @field_validator("output_dir", "log_dir", "data_dir", mode="before")
    @classmethod
    def ensure_path(cls, v: str | Path) -> Path:
        p = Path(v)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def is_tracing_enabled(self) -> bool:
        return self.langchain_tracing_v2 and bool(self.langchain_api_key)


# Module-level singleton — import this everywhere
settings = Settings()
