##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: sop_ingestion.py
# Purpose: SOP document ingestion service + manifest management.
#
#          Gap 5: Indexes SOP files into per-payer ChromaDB collections.
#
#          New (pre-flight proposal):
#            - Manifest file (sop_documents/manifest.json) tracks every
#              payer's collection health: doc count, indexed_at, status.
#            - check_payer_coverage() — validates batch CSV payer IDs
#              against the manifest before the pipeline starts.
#            - verify_collection_query() — test-query for init health check.
#
#          Lifecycle:
#            Setup  → rcm-denial init [--payer X | --all]
#            Query  → pipeline (never triggers indexing)
#            Inspect→ rcm-denial sop-status | rcm-denial init --check-only
#
##########################################################

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rcm_denial.services.audit_service import get_logger
from rcm_denial.tools.sop_rag_tool import (
    _build_or_refresh_collection,
    _load_sop_files,
    _make_embeddings,
    invalidate_payer_cache,
    normalize_payer_id,
)

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Manifest helpers
# ------------------------------------------------------------------ #

def _manifest_path() -> Path:
    from rcm_denial.config.settings import settings
    return settings.sop_documents_dir / "manifest.json"


def read_manifest() -> dict:
    """
    Returns the current manifest dict.
    Structure:
      {
        "last_updated": "<ISO timestamp>",
        "payers": {
          "bcbs": {
            "payer_key":      "bcbs",
            "collection_name":"sop_bcbs",
            "document_count": 12,
            "indexed_at":     "<ISO timestamp>",
            "sop_dir_exists": true,
            "status":         "ok",      // "ok"|"empty"|"missing"|"stale"
            "verified_at":    "<ISO timestamp>",
            "verify_hit_count": 3,
          }, ...
        }
      }
    """
    p = _manifest_path()
    if not p.exists():
        return {"last_updated": None, "payers": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_updated": None, "payers": {}}


def _write_manifest(manifest: dict) -> None:
    manifest["last_updated"] = datetime.utcnow().isoformat()
    _manifest_path().write_text(
        json.dumps(manifest, indent=2, default=str),
        encoding="utf-8",
    )


def _upsert_manifest_entry(
    payer_key: str,
    *,
    document_count: int,
    indexed_at: str,
    sop_dir_exists: bool,
    status: str,
    verified_at: Optional[str] = None,
    verify_hit_count: Optional[int] = None,
) -> None:
    """Update a single payer's entry in the manifest (thread-safe: read-modify-write)."""
    manifest = read_manifest()
    manifest["payers"][payer_key] = {
        "payer_key":        payer_key,
        "collection_name":  f"sop_{payer_key}",
        "document_count":   document_count,
        "indexed_at":       indexed_at,
        "sop_dir_exists":   sop_dir_exists,
        "status":           status,
        "verified_at":      verified_at,
        "verify_hit_count": verify_hit_count,
    }
    _write_manifest(manifest)


# ------------------------------------------------------------------ #
# Verification query
# ------------------------------------------------------------------ #

def verify_collection_query(payer_id: str) -> dict:
    """
    Runs a test query against a payer's collection to confirm it is
    queryable and returns results.

    Returns:
        {
          "payer_key": "bcbs",
          "status": "ok" | "empty" | "missing" | "error",
          "hit_count": 3,
          "message": "...",
        }
    """
    from rcm_denial.config.settings import settings

    payer_key = normalize_payer_id(payer_id)

    try:
        import chromadb
        from langchain_chroma import Chroma

        client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
        collection_name = f"sop_{payer_key}"

        try:
            col_meta = client.get_collection(collection_name)
        except Exception:
            return {
                "payer_key": payer_key,
                "status":    "missing",
                "hit_count": 0,
                "message":   f"Collection '{collection_name}' does not exist",
            }

        if col_meta.count() == 0:
            return {
                "payer_key": payer_key,
                "status":    "empty",
                "hit_count": 0,
                "message":   "Collection exists but has 0 documents",
            }

        embeddings = _make_embeddings()
        if embeddings is None:
            # Can't do a semantic query — just count documents
            return {
                "payer_key": payer_key,
                "status":    "ok",
                "hit_count": col_meta.count(),
                "message":   f"{col_meta.count()} docs indexed (no embeddings to test query)",
            }

        collection = Chroma(
            collection_name=collection_name,
            embedding_function=embeddings,
            persist_directory=str(settings.chroma_persist_dir),
        )
        results = collection.similarity_search("medical necessity appeal procedure", k=3)
        hit_count = len(results)

        return {
            "payer_key": payer_key,
            "status":    "ok" if hit_count > 0 else "empty",
            "hit_count": hit_count,
            "message":   f"{col_meta.count()} docs indexed, test query returned {hit_count} hits",
        }

    except Exception as exc:
        return {
            "payer_key": payer_key,
            "status":    "error",
            "hit_count": 0,
            "message":   str(exc),
        }


# ------------------------------------------------------------------ #
# Coverage check (used by batch_processor pre-flight)
# ------------------------------------------------------------------ #

def check_payer_coverage(payer_ids: list[str]) -> dict:
    """
    Checks the manifest to verify that every payer in the given list
    has a healthy SOP collection.

    Returns:
        {
          "covered":           ["bcbs", "aetna"],      # has ok collection
          "missing":           ["xyz_payer"],           # not in manifest at all
          "degraded":          ["medicaid"],            # in manifest but status != ok
          "coverage_pct":      66.7,
          "details":           { payer_key: manifest_entry | None },
          "all_covered":       False,
        }
    """
    manifest = read_manifest()
    payer_entries = manifest.get("payers", {})

    covered:   list[str] = []
    missing:   list[str] = []
    degraded:  list[str] = []
    details:   dict      = {}

    unique_keys = list({normalize_payer_id(p) for p in payer_ids})

    for payer_key in unique_keys:
        if payer_key == "global":
            continue
        entry = payer_entries.get(payer_key)
        details[payer_key] = entry

        if entry is None:
            missing.append(payer_key)
        elif entry.get("status") == "ok":
            covered.append(payer_key)
        else:
            degraded.append(payer_key)

    total = len(unique_keys)
    coverage_pct = round(100.0 * len(covered) / total, 1) if total else 100.0

    return {
        "covered":      sorted(covered),
        "missing":      sorted(missing),
        "degraded":     sorted(degraded),
        "coverage_pct": coverage_pct,
        "details":      details,
        "all_covered":  len(missing) == 0 and len(degraded) == 0,
    }


# ------------------------------------------------------------------ #
# Core ingestion
# ------------------------------------------------------------------ #

def ingest_sop_documents(
    payer_id: str,
    documents_dir: Optional[str | Path] = None,
    run_verify: bool = False,
) -> int:
    """
    Indexes SOP documents for a specific payer into ChromaDB,
    then updates the manifest with the result.

    Args:
        payer_id:      Payer identifier (e.g. "BCBS", "Aetna", "Medicare").
                       Pass "global" to index into the fallback collection.
        documents_dir: Optional custom path to the SOP document directory.
        run_verify:    If True, run a test query and record hit count in manifest.

    Returns the number of documents indexed (0 if none found or error).
    """
    from rcm_denial.config.settings import settings

    payer_key = normalize_payer_id(payer_id)

    if documents_dir is None:
        payer_dir = settings.sop_documents_dir / payer_key
    else:
        payer_dir = Path(documents_dir)

    logger.info(
        "Starting SOP ingestion",
        payer_id=payer_id,
        payer_key=payer_key,
        documents_dir=str(payer_dir),
    )

    payer_dir.mkdir(parents=True, exist_ok=True)

    embeddings = _make_embeddings()
    if embeddings is None:
        logger.error(
            "Cannot ingest SOPs — OpenAI API key not configured or langchain_openai not installed"
        )
        _upsert_manifest_entry(
            payer_key,
            document_count=0,
            indexed_at=datetime.utcnow().isoformat(),
            sop_dir_exists=payer_dir.exists(),
            status="missing",
        )
        return 0

    docs_raw = _load_sop_files(payer_dir)
    global_dir = settings.sop_documents_dir / "global"
    global_docs = _load_sop_files(global_dir) if payer_key != "global" else []

    total_files = len(docs_raw) + len(global_docs)
    if total_files == 0:
        logger.warning(
            "No SOP documents found — built-in mock SOPs will be used as seed",
            payer_dir=str(payer_dir),
        )

    try:
        collection = _build_or_refresh_collection(payer_key, payer_dir, embeddings)
        if collection is None:
            logger.error("Collection build returned None", payer_key=payer_key)
            _upsert_manifest_entry(
                payer_key,
                document_count=0,
                indexed_at=datetime.utcnow().isoformat(),
                sop_dir_exists=payer_dir.exists(),
                status="empty",
            )
            return 0

        invalidate_payer_cache(payer_id)

        count = collection._collection.count()
        indexed_at = datetime.utcnow().isoformat()

        # Optional post-index verification query
        verified_at   = None
        verify_hits   = None
        status        = "ok" if count > 0 else "empty"

        if run_verify:
            vr = verify_collection_query(payer_key)
            verified_at = datetime.utcnow().isoformat()
            verify_hits = vr["hit_count"]
            if vr["status"] != "ok":
                status = vr["status"]

        _upsert_manifest_entry(
            payer_key,
            document_count=count,
            indexed_at=indexed_at,
            sop_dir_exists=payer_dir.exists(),
            status=status,
            verified_at=verified_at,
            verify_hit_count=verify_hits,
        )

        logger.info(
            "SOP ingestion complete",
            payer_key=payer_key,
            documents_indexed=count,
            status=status,
        )
        return count

    except Exception as exc:
        logger.error("SOP ingestion failed", payer_key=payer_key, error=str(exc))
        _upsert_manifest_entry(
            payer_key,
            document_count=0,
            indexed_at=datetime.utcnow().isoformat(),
            sop_dir_exists=payer_dir.exists(),
            status="error",
        )
        return 0


def _is_collection_fresh(payer_key: str, payer_dir: Path) -> bool:
    """
    Check if a payer's collection is already indexed and up-to-date.
    Returns True if we can skip re-indexing.
    """
    manifest = read_manifest()
    entry = manifest.get("payers", {}).get(payer_key)
    if not entry:
        return False
    if entry.get("status") not in ("ok",):
        return False
    if entry.get("document_count", 0) == 0:
        return False

    # Check if any SOP file is newer than the indexed_at timestamp
    indexed_at = entry.get("indexed_at", "")
    if not indexed_at:
        return False

    try:
        from datetime import datetime as dt
        idx_time = dt.fromisoformat(indexed_at)
        for f in payer_dir.rglob("*"):
            if f.is_file() and f.suffix in (".txt", ".pdf", ".md"):
                file_mtime = dt.fromtimestamp(f.stat().st_mtime)
                if file_mtime > idx_time:
                    return False  # file is newer than index
        return True  # all files older than index
    except Exception:
        return False


def ingest_all_payer_sops(run_verify: bool = False, force: bool = False) -> dict[str, int]:
    """
    Scans data/sop_documents/ for payer subdirectories and indexes each one.
    Updates the manifest with every payer's result.
    Always indexes 'global' first.

    Skips payers whose collections are already indexed and up-to-date
    (unless force=True).

    Returns a dict of {payer_key: documents_indexed}.
    """
    from rcm_denial.config.settings import settings

    sop_root = settings.sop_documents_dir
    sop_root.mkdir(parents=True, exist_ok=True)

    results: dict[str, int] = {}
    skipped: list[str] = []

    global_dir = sop_root / "global"
    global_dir.mkdir(exist_ok=True)
    if not force and _is_collection_fresh("global", global_dir):
        manifest = read_manifest()
        results["global"] = manifest.get("payers", {}).get("global", {}).get("document_count", 0)
        skipped.append("global")
        logger.info("SOP collection already fresh — skipping", payer_key="global")
    else:
        results["global"] = ingest_sop_documents("global", run_verify=run_verify)

    for payer_dir in sorted(sop_root.iterdir()):
        if not payer_dir.is_dir():
            continue
        payer_key = payer_dir.name
        if payer_key == "global":
            continue

        if not force and _is_collection_fresh(payer_key, payer_dir):
            manifest = read_manifest()
            results[payer_key] = manifest.get("payers", {}).get(payer_key, {}).get("document_count", 0)
            skipped.append(payer_key)
            logger.info("SOP collection already fresh — skipping", payer_key=payer_key)
        else:
            count = ingest_sop_documents(payer_key, documents_dir=payer_dir, run_verify=run_verify)
            results[payer_key] = count

    total = sum(results.values())
    logger.info(
        "All payer SOP ingestions complete",
        payer_count=len(results),
        total_documents=total,
        skipped=skipped,
        rebuilt=len(results) - len(skipped),
    )
    return results


# ------------------------------------------------------------------ #
# Stats (used by sop-status CLI)
# ------------------------------------------------------------------ #

def get_collection_stats() -> list[dict]:
    """
    Returns stats for all existing SOP ChromaDB collections.
    Merges live ChromaDB counts with manifest metadata.
    """
    from rcm_denial.config.settings import settings

    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    except Exception as exc:
        logger.warning("ChromaDB unavailable for stats", error=str(exc))
        return []

    manifest_entries = read_manifest().get("payers", {})
    stats = []

    try:
        collections = client.list_collections()
    except Exception:
        return []

    for col in collections:
        if not col.name.startswith("sop_"):
            continue
        payer_key = col.name[4:]
        meta = col.metadata or {}
        indexed_at_ts = meta.get("indexed_at")
        indexed_at = str(indexed_at_ts) if indexed_at_ts else "unknown"
        sop_dir = settings.sop_documents_dir / payer_key
        manifest_entry = manifest_entries.get(payer_key, {})

        stats.append({
            "payer_key":       payer_key,
            "collection_name": col.name,
            "document_count":  col.count(),
            "indexed_at":      indexed_at,
            "sop_dir_exists":  sop_dir.exists(),
            "sop_dir_path":    str(sop_dir),
            "status":          manifest_entry.get("status", "unknown"),
            "verified_at":     manifest_entry.get("verified_at"),
            "verify_hit_count":manifest_entry.get("verify_hit_count"),
        })

    return sorted(stats, key=lambda s: s["payer_key"])
