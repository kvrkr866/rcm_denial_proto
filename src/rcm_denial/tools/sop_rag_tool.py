##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: sop_rag_tool.py
# Purpose: RAG (Retrieval Augmented Generation) tool that
#          queries a ChromaDB vector store of SOP documents
#          to retrieve relevant denial handling procedures.
#          Falls back to keyword-based retrieval if ChromaDB
#          or OpenAI embeddings are unavailable.
#
##########################################################

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rcm_denial.models.claim import SopResult
from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


# ------------------------------------------------------------------ #
# Fallback mock SOP library (used when ChromaDB unavailable)
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
        "payer_applicability": ["BCBS", "AETNA", "CIGNA"],
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
        "payer_applicability": ["MEDICARE", "BCBS", "AETNA"],
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
]


def _get_vector_store():
    """
    Initializes and returns a ChromaDB collection with OpenAI embeddings.
    Returns None if dependencies are unavailable — fallback kicks in.
    """
    try:
        import chromadb
        from langchain_chroma import Chroma
        from langchain_openai import OpenAIEmbeddings
        from rcm_denial.config.settings import settings

        if not settings.openai_api_key:
            return None

        embeddings = OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            openai_api_key=settings.openai_api_key,
        )
        vector_store = Chroma(
            collection_name=settings.chroma_collection_name,
            embedding_function=embeddings,
            persist_directory=str(settings.chroma_persist_dir),
        )
        return vector_store
    except Exception as exc:
        logger.warning("ChromaDB unavailable — falling back to keyword search", error=str(exc))
        return None


def _keyword_fallback(carc_code: str, rarc_code: Optional[str], payer_id: str, top_k: int = 3) -> list[SopResult]:
    """Keyword-based SOP matching when vector store is unavailable."""
    results = []
    query_codes = {carc_code.upper()}
    if rarc_code:
        query_codes.add(rarc_code.upper())

    for sop in _MOCK_SOPS:
        carc_match = any(c in sop["carc_applicability"] for c in query_codes)
        payer_match = (
            "ALL" in sop["payer_applicability"] or
            payer_id.upper() in sop["payer_applicability"]
        )
        if carc_match or payer_match:
            score = 0.9 if (carc_match and payer_match) else 0.7 if carc_match else 0.5
            results.append(SopResult(
                source=sop["source"],
                title=sop["title"],
                content_snippet=sop["content"][:500],
                relevance_score=score,
                carc_applicability=sop["carc_applicability"],
                payer_applicability=sop["payer_applicability"],
            ))

    return sorted(results, key=lambda r: r.relevance_score, reverse=True)[:top_k]


def retrieve_sop_guidance(
    carc_code: str,
    rarc_code: Optional[str] = None,
    payer_id: str = "",
    top_k: int = 3,
) -> list[SopResult]:
    """
    Retrieves relevant SOP documents from the vector store using semantic search.

    Args:
        carc_code: Primary CARC denial code.
        rarc_code: Optional RARC remark code.
        payer_id: Payer identifier for payer-specific SOPs.
        top_k: Maximum number of results to return.

    Returns:
        List of SopResult objects ranked by relevance.

    Raises:
        ToolExecutionError: If retrieval fails completely.
    """
    logger.info(
        "Retrieving SOP guidance",
        carc_code=carc_code,
        rarc_code=rarc_code,
        payer_id=payer_id,
    )

    try:
        vector_store = _get_vector_store()

        if vector_store is None:
            logger.info("Using keyword-based SOP fallback")
            results = _keyword_fallback(carc_code, rarc_code, payer_id, top_k=top_k)
        else:
            query = (
                f"denial management procedure for CARC {carc_code} "
                f"{'RARC ' + rarc_code if rarc_code else ''} "
                f"payer {payer_id}"
            )
            docs = vector_store.similarity_search_with_score(query, k=top_k)
            results = [
                SopResult(
                    source=doc.metadata.get("source", "Unknown"),
                    title=doc.metadata.get("title", "SOP Document"),
                    content_snippet=doc.page_content[:500],
                    relevance_score=max(0.0, min(1.0, 1.0 - score)),
                    carc_applicability=doc.metadata.get("carc_codes", []),
                    payer_applicability=doc.metadata.get("payer_ids", []),
                )
                for doc, score in docs
            ]

        logger.info("SOP retrieval complete", result_count=len(results))
        return results

    except Exception as exc:
        raise ToolExecutionError(f"SOP retrieval failed: {exc}") from exc
