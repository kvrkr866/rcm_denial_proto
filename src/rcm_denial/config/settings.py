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
    # ChromaDB / Vector store
    # ------------------------------------------------------------------ #
    chroma_persist_dir: Path = Field(default=Path("./data/chroma_db"))
    chroma_collection_name: str = Field(default="denial_sop_kb")

    # ------------------------------------------------------------------ #
    # OCR
    # ------------------------------------------------------------------ #
    tesseract_cmd: str = Field(default="/usr/bin/tesseract")
    ocr_dpi: int = Field(default=300)

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
        return self.data_dir / "sop_documents"

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
