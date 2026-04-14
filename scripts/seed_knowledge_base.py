##########################################################
#
# Project: RCM - Denial Management
# Description: Agentic AI based Denial management activities
# Author:  RK (kvrkr866@gmail.com)
# File name: seed_knowledge_base.py
# Purpose: Seeds the ChromaDB vector store with SOP documents
#          from data/sop_documents/. Run once before first use
#          or after adding new SOP documents.
#          Usage: python scripts/seed_knowledge_base.py
#
##########################################################

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rcm_denial.services.audit_service import get_logger

logger = get_logger(__name__)


def parse_sop_metadata(text: str, filename: str) -> dict:
    """Extracts metadata fields from SOP file header."""
    metadata = {
        "source": filename,
        "title": filename,
        "carc_codes": [],
        "payer_ids": [],
    }
    for line in text.split("\n")[:6]:
        if line.startswith("TITLE:"):
            metadata["title"] = line.replace("TITLE:", "").strip()
        elif line.startswith("SOURCE:"):
            metadata["source"] = line.replace("SOURCE:", "").strip()
        elif line.startswith("CARC_CODES:"):
            codes_str = line.replace("CARC_CODES:", "").strip()
            metadata["carc_codes"] = [c.strip() for c in codes_str.split(",")]
        elif line.startswith("PAYER_IDS:"):
            payers_str = line.replace("PAYER_IDS:", "").strip()
            metadata["payer_ids"] = [p.strip() for p in payers_str.split(",")]
    return metadata


def seed_knowledge_base() -> int:
    """
    Loads all .txt SOP documents and upserts them into ChromaDB.
    Returns the number of documents indexed.
    """
    try:
        from langchain_chroma import Chroma
        from langchain_openai import OpenAIEmbeddings
        from langchain_core.documents import Document
        from rcm_denial.config.settings import settings

        if not settings.openai_api_key:
            logger.error("OPENAI_API_KEY not set — cannot create embeddings")
            print("ERROR: Set OPENAI_API_KEY in .env before seeding the knowledge base.")
            return 0

        sop_dir = settings.sop_documents_dir
        sop_files = list(sop_dir.glob("*.txt"))

        if not sop_files:
            logger.warning("No SOP documents found", directory=str(sop_dir))
            print(f"No .txt files found in {sop_dir}")
            return 0

        print(f"Found {len(sop_files)} SOP documents to index...")

        embeddings = OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            openai_api_key=settings.openai_api_key,
        )

        documents = []
        for sop_file in sop_files:
            text = sop_file.read_text(encoding="utf-8")
            metadata = parse_sop_metadata(text, sop_file.stem)
            documents.append(Document(page_content=text, metadata=metadata))
            print(f"  Loaded: {metadata['title']} ({metadata['source']})")

        # Upsert into ChromaDB
        vector_store = Chroma.from_documents(
            documents=documents,
            embedding=embeddings,
            collection_name=settings.chroma_collection_name,
            persist_directory=str(settings.chroma_persist_dir),
        )

        print(f"\n✓ Successfully indexed {len(documents)} SOP documents into ChromaDB")
        print(f"  Collection: {settings.chroma_collection_name}")
        print(f"  Persist dir: {settings.chroma_persist_dir}")

        logger.info(
            "Knowledge base seeded",
            document_count=len(documents),
            collection=settings.chroma_collection_name,
        )
        return len(documents)

    except ImportError as exc:
        print(f"ERROR: Required package not installed: {exc}")
        print("Run: pip install langchain-chroma langchain-openai")
        return 0
    except Exception as exc:
        logger.error("Knowledge base seeding failed", error=str(exc))
        print(f"ERROR: {exc}")
        return 0


if __name__ == "__main__":
    count = seed_knowledge_base()
    sys.exit(0 if count > 0 else 1)
