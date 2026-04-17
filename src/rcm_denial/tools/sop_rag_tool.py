##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: sop_rag_tool.py
# Purpose: Per-payer SOP RAG tool.
#
#          Phase 3 — Gaps 2-7:
#            Gap 2: One ChromaDB collection per payer (sop_{payer_id})
#            Gap 3: Lazy build — collection created on first use, not startup
#            Gap 4: Payer ID normalization (BCBS / BlueCross → bcbs)
#            Gap 5: Ingest SOP files from data/sop_documents/{payer_id}/
#            Gap 6: Freshness check — re-index when SOP files are newer
#            Gap 7: Keyword fallback when no collection exists for payer
#
#          Collection naming:
#            sop_global  — generic SOPs (all payers)
#            sop_bcbs    — BCBS-specific SOPs
#            sop_aetna   — Aetna-specific SOPs
#            etc.
#
#          SOP directory layout:
#            data/sop_documents/
#              global/   ← applies to all payers
#              bcbs/     ← BCBS-specific
#              aetna/    ← Aetna-specific
#              ...
#
##########################################################

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rcm_denial.models.claim import SopResult
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


# ------------------------------------------------------------------ #
# Gap 4 — Payer ID normalization
# Maps common payer name variants to canonical lowercase keys.
# These keys become the collection name suffix: sop_{key}
# ------------------------------------------------------------------ #

_PAYER_ALIASES: dict[str, str] = {
    # BCBS variants
    "bluecross": "bcbs",
    "blue cross": "bcbs",
    "blue shield": "bcbs",
    "bcbs": "bcbs",
    "bc/bs": "bcbs",
    "anthem": "bcbs",
    "anthem bcbs": "bcbs",
    # Aetna
    "aetna": "aetna",
    "cvs aetna": "aetna",
    # UnitedHealth / UHC
    "unitedhealthcare": "uhc",
    "united health": "uhc",
    "uhc": "uhc",
    "optum": "uhc",
    # Cigna
    "cigna": "cigna",
    "cigna healthspring": "cigna",
    # Humana
    "humana": "humana",
    # Medicare / CMS
    "medicare": "medicare",
    "cms": "medicare",
    "novitas": "medicare",
    "noridian": "medicare",
    "palmetto": "medicare",
    "ngsmedicare": "medicare",
    # Medicaid (generic — state-specific would need more entries)
    "medicaid": "medicaid",
    # Tricare / Military
    "tricare": "tricare",
    "champus": "tricare",
    # Molina
    "molina": "molina",
    # Centene / WellCare
    "centene": "centene",
    "wellcare": "centene",
    # Kaiser
    "kaiser": "kaiser",
    "kp": "kaiser",
}


def normalize_payer_id(payer_id: str) -> str:
    """
    Returns a canonical lowercase payer key for use as a collection name.

    Examples:
      "BCBS"            → "bcbs"
      "BlueCross"       → "bcbs"
      "UnitedHealthcare"→ "uhc"
      "UNKNOWN_PAYER"   → "unknown_payer"   (lowercased, kept as-is)
      ""                → "global"
    """
    if not payer_id:
        return "global"
    normalized = payer_id.strip().lower().replace("-", " ").replace("_", " ")
    return _PAYER_ALIASES.get(normalized, normalized.replace(" ", "_"))


def _collection_name(payer_id: str) -> str:
    """Returns the ChromaDB collection name for a payer."""
    return f"sop_{normalize_payer_id(payer_id)}"


# ------------------------------------------------------------------ #
# Fallback keyword SOP library (Gap 7)
# Used when no ChromaDB collection exists for the payer.
# ------------------------------------------------------------------ #

_MOCK_SOPS: list[dict] = [
    {
        "title": "Timely Filing Appeal Procedure",
        "source": "SOP-TF-001",
        "carc_applicability": ["29"],
        "payer_applicability": ["ALL"],
        "content": (
            "For timely filing denials (CARC 29): "
            "1) Obtain proof of timely submission — system logs, clearinghouse reports, or certified mail receipts. "
            "2) Submit Level 1 appeal within payer deadline with proof of timely filing attached. "
            "3) Include cover letter citing date of original submission. "
            "4) If no proof available, escalate to billing manager for write-off determination. "
            "Required documents: Original claim copy, clearinghouse transmission report, appeal letter."
        ),
    },
    {
        "title": "Prior Authorization Appeal Procedure",
        "source": "SOP-AUTH-002",
        "carc_applicability": ["97", "96", "167"],
        "payer_applicability": ["bcbs", "aetna", "cigna", "ALL"],
        "content": (
            "For prior authorization denials (CARC 97/96/167): "
            "1) Verify whether auth was obtained. Check EHR auth records. "
            "2) If auth exists: attach authorization number and letter to appeal. "
            "3) If no auth: clinical staff must submit retro-auth request simultaneously. "
            "4) Attach clinical documentation including physician notes and medical necessity letter. "
            "5) Reference payer LCD/NCD policies supporting medical necessity. "
            "Appeal deadline: 180 days from denial date for most commercial payers."
        ),
    },
    {
        "title": "Medical Necessity Documentation Guide",
        "source": "SOP-MN-003",
        "carc_applicability": ["50", "55", "167", "170"],
        "payer_applicability": ["medicare", "bcbs", "aetna", "ALL"],
        "content": (
            "Medical necessity denials require the following documentation: "
            "1) Complete physician progress notes from date of service. "
            "2) Diagnostic test results supporting diagnosis codes. "
            "3) Treatment history showing conservative therapy was attempted. "
            "4) Physician letter of medical necessity on letterhead. "
            "5) Reference relevant Medicare LCD (Local Coverage Determination) or NCD. "
            "For Medicare: cite CMS IOM Publication 100-08, Medicare Program Integrity Manual."
        ),
    },
    {
        "title": "Coding Error Correction Workflow",
        "source": "SOP-CODE-004",
        "carc_applicability": ["4", "11", "16", "22"],
        "payer_applicability": ["ALL"],
        "content": (
            "For coding-related denials: "
            "1) Pull original claim and identify flagged codes. "
            "2) Coding team reviews CPT, ICD-10, and modifier usage. "
            "3) Correct codes per AMA CPT guidelines and CMS ICD-10-CM guidelines. "
            "4) Verify correct place-of-service code (POS). "
            "5) Resubmit corrected claim within timely filing window. "
            "Note: Do not appeal coding errors — resubmit corrected claim. "
            "Common errors: unbundling (CARC 97), incorrect modifier (CARC 4/22)."
        ),
    },
    {
        "title": "Duplicate Claim Resolution Procedure",
        "source": "SOP-DUP-005",
        "carc_applicability": ["18", "B7"],
        "payer_applicability": ["ALL"],
        "content": (
            "For duplicate claim denials (CARC 18): "
            "1) Verify whether original claim was paid, denied, or pending. "
            "2) If original was paid: close case, apply payment to account. "
            "3) If original is pending: wait for adjudication, do not resubmit. "
            "4) If original was denied for different reason: "
            "   a) Add frequency code modifier (e.g. -76 for repeat procedure). "
            "   b) Include written explanation distinguishing this from prior claim. "
            "5) Attach original EOB if available."
        ),
    },
    {
        "title": "Eligibility / Coverage Denial Response",
        "source": "SOP-ELIG-006",
        "carc_applicability": ["27", "1", "119"],
        "payer_applicability": ["ALL"],
        "content": (
            "For eligibility and coverage denials (CARC 27/1/119): "
            "1) Verify member eligibility on date of service via payer portal or 270/271 EDI. "
            "2) Confirm correct member ID and group number were submitted. "
            "3) If member was eligible: submit corrected claim with proof of eligibility. "
            "4) If benefit maximum reached (CARC 119): document medical necessity and request exception. "
            "5) Coordinate with patient registration team to verify insurance data at point of service."
        ),
    },
    {
        "title": "Coordination of Benefits (COB) Procedure",
        "source": "SOP-COB-007",
        "carc_applicability": ["22", "11", "B11"],
        "payer_applicability": ["ALL"],
        "content": (
            "For COB denials: "
            "1) Determine primary vs secondary payer order. "
            "2) Submit to primary payer first if not already done. "
            "3) Attach primary payer EOB/EOMB to secondary claim submission. "
            "4) For Medicare secondary: attach Medicare Summary Notice (MSN). "
            "5) Verify COB information with patient if payer order is unclear."
        ),
    },
]


def _keyword_fallback(
    carc_code: str,
    rarc_code: Optional[str],
    payer_id: str,
    top_k: int = 3,
) -> list[SopResult]:
    """
    Gap 7 — keyword-based SOP matching when no vector collection exists.

    Scores by: (1) CARC match, (2) payer match (normalized), (3) 'ALL' applicability.
    Returns top_k results sorted by score descending.
    """
    normalized_payer = normalize_payer_id(payer_id)
    query_codes = {carc_code.upper().strip()}
    if rarc_code:
        query_codes.add(rarc_code.upper().strip())

    results = []
    for sop in _MOCK_SOPS:
        carc_match = any(c in sop["carc_applicability"] for c in query_codes)
        payer_match = (
            normalized_payer in [p.lower() for p in sop["payer_applicability"]] or
            "ALL" in sop["payer_applicability"]
        )
        if not (carc_match or payer_match):
            continue

        if carc_match and payer_match and normalized_payer != "global":
            score = 0.92
        elif carc_match and payer_match:
            score = 0.85
        elif carc_match:
            score = 0.72
        else:
            score = 0.50

        results.append(SopResult(
            source=sop["source"],
            title=sop["title"],
            content_snippet=sop["content"][:600],
            relevance_score=score,
            carc_applicability=sop["carc_applicability"],
            payer_applicability=sop["payer_applicability"],
        ))

    return sorted(results, key=lambda r: r.relevance_score, reverse=True)[:top_k]


# ------------------------------------------------------------------ #
# Gap 5 — SOP file loader (PDF, txt, md)
# Reads SOP files from data/sop_documents/{payer_id}/
# ------------------------------------------------------------------ #

def _load_sop_files(payer_dir: Path) -> list[dict]:
    """
    Loads SOP documents from a payer-specific directory.

    Supported formats:
      .txt / .md  — read as plain text
      .pdf        — extracted via pytesseract (optional; skipped if unavailable)
      .json       — array of {title, content, carc_codes, payer_ids} dicts

    Returns a list of document dicts:
      {title, content, source, carc_codes, payer_ids, file_path}
    """
    if not payer_dir.exists():
        return []

    documents = []
    for filepath in sorted(payer_dir.iterdir()):
        if not filepath.is_file():
            continue

        ext = filepath.suffix.lower()
        try:
            if ext in (".txt", ".md"):
                content = filepath.read_text(encoding="utf-8")
                documents.append({
                    "title": filepath.stem.replace("_", " ").title(),
                    "content": content,
                    "source": filepath.name,
                    "carc_codes": [],
                    "payer_ids": [],
                    "file_path": str(filepath),
                })

            elif ext == ".json":
                raw = json.loads(filepath.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        if "content" in item:
                            item.setdefault("source", filepath.name)
                            item.setdefault("file_path", str(filepath))
                            documents.append(item)
                elif isinstance(raw, dict) and "content" in raw:
                    raw.setdefault("source", filepath.name)
                    raw.setdefault("file_path", str(filepath))
                    documents.append(raw)

            elif ext == ".pdf":
                content = None
                # Strategy 1: PyMuPDF (fast, accurate for digital PDFs)
                try:
                    import fitz
                    doc = fitz.open(str(filepath))
                    pages_text = [page.get_text() for page in doc]
                    doc.close()
                    text = "\n".join(pages_text).strip()
                    if len(text) >= 50:
                        content = text
                except ImportError:
                    pass
                except Exception:
                    pass

                # Strategy 2: Tesseract fallback (scanned PDFs)
                if content is None:
                    try:
                        import pytesseract
                        from pdf2image import convert_from_path
                        pages = convert_from_path(str(filepath), dpi=200)
                        content = "\n".join(pytesseract.image_to_string(p) for p in pages)
                    except ImportError:
                        logger.warning("PDF extraction unavailable — skipping SOP PDF",
                                       file=filepath.name)
                    except Exception as pdf_exc:
                        logger.warning("PDF extraction failed", file=filepath.name, error=str(pdf_exc))

                if content:
                    documents.append({
                        "title": filepath.stem.replace("_", " ").title(),
                        "content": content,
                        "source": filepath.name,
                        "carc_codes": [],
                        "payer_ids": [],
                        "file_path": str(filepath),
                    })

        except Exception as exc:
            logger.warning("Failed to load SOP file", file=str(filepath), error=str(exc))

    logger.info("SOP files loaded", payer_dir=str(payer_dir), count=len(documents))
    return documents


# ------------------------------------------------------------------ #
# Gap 6 — Freshness check
# ------------------------------------------------------------------ #

def _get_collection_indexed_at(collection) -> Optional[float]:
    """
    Returns the Unix timestamp when the collection was last indexed,
    stored as collection metadata under key 'indexed_at'.
    Returns None if metadata not found (collection exists but predates freshness tracking).

    LangChain Chroma wraps the raw collection as _collection; metadata
    lives on the raw collection object, not on the LangChain wrapper.
    """
    try:
        # LangChain Chroma wrapper exposes the raw chromadb collection as ._collection
        raw = getattr(collection, "_collection", None)
        if raw is not None:
            meta = raw.metadata or {}
        else:
            # Fallback: some versions expose .metadata directly
            meta = getattr(collection, "metadata", None) or {}
        ts = meta.get("indexed_at")
        return float(ts) if ts else None
    except Exception:
        return None


def _get_sop_dir_mtime(payer_dir: Path) -> float:
    """Returns the most recent modification time of any file in payer_dir."""
    if not payer_dir.exists():
        return 0.0
    mtimes = [f.stat().st_mtime for f in payer_dir.rglob("*") if f.is_file()]
    return max(mtimes) if mtimes else 0.0


def _is_collection_stale(collection, payer_dir: Path) -> bool:
    """
    Returns True if the SOP directory has files newer than the collection's
    last index time, meaning the collection needs to be rebuilt.
    """
    indexed_at = _get_collection_indexed_at(collection)
    if indexed_at is None:
        return True   # No timestamp → assume stale
    dir_mtime = _get_sop_dir_mtime(payer_dir)
    return dir_mtime > indexed_at


# ------------------------------------------------------------------ #
# Gap 3 — Lazy collection builder
# Module-level cache: payer_key → (collection | None)
# ------------------------------------------------------------------ #

_collection_cache: dict[str, object] = {}  # payer_key → Chroma collection or None sentinel

# Pipeline mode: when True, never build/refresh collections mid-run.
# Set by batch_processor before the main loop; cleared after.
# Missing collections fall back to global → keyword (non-fatal).
_pipeline_mode: bool = False


def set_pipeline_mode(enabled: bool) -> None:
    """
    Toggle pipeline-mode: when True, sop_rag_tool will never trigger
    indexing during a claim run. Missing collections fall through to
    global/keyword fallback without blocking or rebuilding.

    Called by batch_processor at the start/end of every batch.
    """
    global _pipeline_mode
    _pipeline_mode = enabled
    logger.info("SOP RAG pipeline mode", enabled=enabled)


def _build_or_refresh_collection(payer_key: str, payer_dir: Path, embeddings):
    """
    Creates or refreshes the ChromaDB collection for a payer.
    Loads SOP documents from payer_dir, also merges global/ SOPs.
    Stamps the collection with indexed_at timestamp.
    """
    import chromadb
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from rcm_denial.config.settings import settings

    collection_name = f"sop_{payer_key}"
    logger.info("Building SOP collection", collection=collection_name, payer_dir=str(payer_dir))

    # Load payer-specific docs + global docs
    global_dir = settings.sop_documents_dir / "global"
    docs_raw = _load_sop_files(payer_dir) + _load_sop_files(global_dir)

    # If no files at all, seed with built-in mock SOPs filtered to this payer
    if not docs_raw:
        logger.info("No SOP files found — seeding with built-in SOPs", payer_key=payer_key)
        for sop in _MOCK_SOPS:
            payer_match = (
                payer_key in [p.lower() for p in sop["payer_applicability"]] or
                "ALL" in sop["payer_applicability"]
            )
            if payer_match:
                docs_raw.append({
                    "title": sop["title"],
                    "content": sop["content"],
                    "source": sop["source"],
                    "carc_codes": sop["carc_applicability"],
                    "payer_ids": sop["payer_applicability"],
                    "file_path": "builtin",
                })

    if not docs_raw:
        logger.warning("No SOP documents to index", payer_key=payer_key)
        return None

    lc_docs = [
        Document(
            page_content=d["content"],
            metadata={
                "title":    d.get("title", "SOP Document"),
                "source":   d.get("source", "unknown"),
                "carc_codes": json.dumps(d.get("carc_codes", [])),
                "payer_ids":  json.dumps(d.get("payer_ids", [])),
                "payer_key":  payer_key,
                "file_path":  d.get("file_path", ""),
            },
        )
        for d in docs_raw
    ]

    # Delete existing collection so we can rebuild it cleanly
    try:
        client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
    except Exception:
        pass

    collection = Chroma.from_documents(
        documents=lc_docs,
        embedding=embeddings,
        collection_name=collection_name,
        persist_directory=str(settings.chroma_persist_dir),
        collection_metadata={"indexed_at": str(time.time()), "payer_key": payer_key},
    )

    logger.info(
        "SOP collection built",
        collection=collection_name,
        doc_count=len(lc_docs),
    )
    return collection


def _get_payer_collection(payer_key: str, payer_dir: Path, embeddings):
    """
    Gap 3 — Lazy getter: returns the cached collection or builds it on first use.
    Gap 6 — Freshness: rebuilds collection if SOP files are newer (when enabled).
    """
    from rcm_denial.config.settings import settings
    from langchain_chroma import Chroma

    collection_name = f"sop_{payer_key}"

    # Return from cache if fresh
    if payer_key in _collection_cache:
        cached = _collection_cache[payer_key]
        if cached is None:
            return None   # previously failed — don't retry each claim
        if settings.sop_rag_freshness_check and _is_collection_stale(cached, payer_dir):
            logger.info("SOP collection stale — rebuilding", payer_key=payer_key)
            _collection_cache.pop(payer_key)
        else:
            return cached

    # Try to open existing persisted collection
    try:
        collection = Chroma(
            collection_name=collection_name,
            embedding_function=embeddings,
            persist_directory=str(settings.chroma_persist_dir),
        )
        # Check if it actually has documents
        if collection._collection.count() == 0:
            raise ValueError("empty collection")

        # Freshness check on existing collection
        if settings.sop_rag_freshness_check and _is_collection_stale(collection, payer_dir):
            if _pipeline_mode:
                # In pipeline mode: warn but use the stale collection rather than rebuilding
                logger.warning(
                    "SOP collection stale but pipeline-mode is on — "
                    "using existing collection; run 'rcm-denial init' to refresh",
                    payer_key=payer_key,
                )
            else:
                logger.info("SOP collection stale — rebuilding", payer_key=payer_key)
                raise ValueError("stale collection")

        logger.info("SOP collection loaded from disk", collection=collection_name)
        _collection_cache[payer_key] = collection
        return collection

    except Exception:
        pass

    # In pipeline mode: never build — fall back to global/keyword
    if _pipeline_mode:
        logger.warning(
            "SOP collection missing in pipeline mode — "
            "falling back to global/keyword; run 'rcm-denial init' to build",
            payer_key=payer_key,
        )
        _collection_cache[payer_key] = None
        return None

    # Build from scratch (init-time only)
    collection = _build_or_refresh_collection(payer_key, payer_dir, embeddings)
    _collection_cache[payer_key] = collection   # None if build failed
    return collection


def _make_embeddings():
    """Returns an OpenAI embeddings instance, or None if unavailable."""
    try:
        from langchain_openai import OpenAIEmbeddings
        from rcm_denial.config.settings import settings
        if not settings.openai_api_key:
            return None
        return OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            openai_api_key=settings.openai_api_key,
        )
    except ImportError:
        return None


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def retrieve_sop_guidance(
    carc_code: str,
    rarc_code: Optional[str] = None,
    payer_id: str = "",
    denial_description: str = "",
    top_k: int = 5,
) -> list[SopResult]:
    """
    Retrieves relevant SOP documents using semantic search.

    Uses BOTH denial codes AND the denial description for precise matching.
    This ensures the SOP results are specific to the exact denial scenario
    (e.g., CO-252 + M127 "missing medical record" finds the SOP section
    about retrieving operative reports, not the generic CO-252 overview).

    Lookup order (per-payer → global → keyword):
      1. Payer-specific ChromaDB collection (sop_{payer_key})
      2. Global collection (sop_global) — if payer collection has no results
      3. Keyword fallback — if ChromaDB unavailable

    Args:
        carc_code: Primary CARC denial code (e.g., "CO-252" or "252").
        rarc_code: RARC remark code (e.g., "M127").
        payer_id:  Payer identifier — normalized to collection key.
        denial_description: Natural language description of what's missing
                            (e.g., "missing medical record"). Improves SOP match accuracy.
        top_k:     Maximum results to return.

    Returns:
        List of SopResult ranked by relevance (best first).
    """
    from rcm_denial.config.settings import settings

    payer_key = normalize_payer_id(payer_id)

    logger.info(
        "Retrieving SOP guidance",
        carc_code=carc_code,
        rarc_code=rarc_code,
        payer_id=payer_id,
        payer_key=payer_key,
        denial_description=denial_description[:50] if denial_description else "",
    )

    # Build a rich query with both codes AND description for precise matching
    query_parts = [f"denial resolution procedure CO-{carc_code}"]
    if rarc_code:
        query_parts.append(f"remark code {rarc_code}")
    if denial_description:
        query_parts.append(denial_description)
    else:
        query_parts.append(f"payer {payer_id}")
    query = " ".join(query_parts)

    try:
        embeddings = _make_embeddings()

        if embeddings is None:
            logger.info("Embeddings unavailable — using keyword SOP fallback")
            return _keyword_fallback(carc_code, rarc_code, payer_id, top_k=top_k)

        # ---- Gap 2: Try payer-specific collection first ----
        payer_dir = settings.sop_documents_dir / payer_key
        collection = _get_payer_collection(payer_key, payer_dir, embeddings)

        results: list[SopResult] = []

        if collection is not None:
            results = _query_collection(collection, query, top_k, carc_code, rarc_code)

        # ---- If payer collection empty/missing, try global ----
        if not results and payer_key != "global":
            logger.info("No payer-specific SOP results — trying global collection", payer_key=payer_key)
            global_dir = settings.sop_documents_dir / "global"
            global_col = _get_payer_collection("global", global_dir, embeddings)
            if global_col:
                results = _query_collection(global_col, query, top_k, carc_code, rarc_code)

        # ---- Gap 7: Final fallback to keyword search ----
        if not results:
            logger.info("No RAG results — falling back to keyword search", payer_key=payer_key)
            results = _keyword_fallback(carc_code, rarc_code, payer_id, top_k=top_k)

        logger.info("SOP retrieval complete", result_count=len(results), payer_key=payer_key)
        return results

    except Exception as exc:
        raise ToolExecutionError(f"SOP retrieval failed: {exc}") from exc


def _query_collection(
    collection,
    query: str,
    top_k: int,
    carc_code: str,
    rarc_code: Optional[str],
) -> list[SopResult]:
    """
    Queries a ChromaDB collection and converts results to SopResult objects.
    Filters out hits below settings.sop_min_relevance_score.
    """
    from rcm_denial.config.settings import settings

    try:
        docs_and_scores = collection.similarity_search_with_score(query, k=top_k)
    except Exception as exc:
        logger.warning("Collection query failed", error=str(exc))
        return []

    results = []
    for doc, distance in docs_and_scores:
        # ChromaDB L2 distance → relevance: lower distance = higher relevance
        relevance = max(0.0, min(1.0, 1.0 - distance))
        if relevance < settings.sop_min_relevance_score:
            continue
        meta = doc.metadata or {}
        try:
            carc_list = json.loads(meta.get("carc_codes", "[]"))
        except (json.JSONDecodeError, TypeError):
            carc_list = []
        try:
            payer_list = json.loads(meta.get("payer_ids", "[]"))
        except (json.JSONDecodeError, TypeError):
            payer_list = []
        results.append(SopResult(
            source=meta.get("source", "Unknown"),
            title=meta.get("title", "SOP Document"),
            content_snippet=doc.page_content[:600],
            relevance_score=relevance,
            carc_applicability=carc_list,
            payer_applicability=payer_list,
        ))

    return sorted(results, key=lambda r: r.relevance_score, reverse=True)


def invalidate_payer_cache(payer_id: str = "") -> None:
    """
    Clears the in-memory collection cache for a payer (or all payers).
    Call after ingesting new SOP documents to force a rebuild on next use.
    """
    if payer_id:
        payer_key = normalize_payer_id(payer_id)
        _collection_cache.pop(payer_key, None)
        logger.info("SOP cache invalidated", payer_key=payer_key)
    else:
        _collection_cache.clear()
        logger.info("SOP cache fully cleared")
