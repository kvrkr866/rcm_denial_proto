# RCM Denial Management — Agentic AI System

**Project:** RCM - Denial Management  
**Author:** RK (kvrkr866@gmail.com)  
**Version:** 1.0.0

A production-grade, multi-agent AI system built with **LangGraph** that automates medical claim denial management for USA hospitals. The system analyzes denied claims using CARC/RARC codes, enriches them with data from multiple sources, and generates resubmission or appeal packages.

---

## Architecture Overview

```
CSV Input → Intake Agent → Enrichment Agent → Analysis Agent
                                                     │
                                              Supervisor Router
                                           ┌──────┴──────────┐
                                    Correction          Appeal Prep
                                    Plan Agent            Agent
                                           └──────┬──────────┘
                                         Document Packaging Agent
                                                     │
                                          PDF Package + JSON Output
```

### Agents

| Agent | Node Name | Responsibility |
|---|---|---|
| Intake | `intake_agent` | Validate and parse ClaimRecord from CSV row |
| Enrichment | `enrichment_agent` | Fan-out to 5 data sources in parallel |
| Analysis | `analysis_agent` | LLM-powered CARC/RARC root cause analysis |
| Correction Plan | `correction_plan_agent` | Code corrections + documentation checklist |
| Appeal Prep | `appeal_prep_agent` | LLM-generated appeal letter + evidence bundle |
| Packaging | `document_packaging_agent` | PDF generation, merge, metadata JSON |

### Data Sources (Mock → Real Integration Path)

| Source | Tool File | Real Integration |
|---|---|---|
| Patient Data Service | `tools/patient_data_tool.py` | FHIR R4 `/Patient` + `/Coverage` |
| Payer Policy | `tools/payer_policy_tool.py` | Availity / Change Healthcare |
| Provider EHR | `tools/ehr_tool.py` | Epic FHIR, Cerner, Athena |
| EOB OCR | `tools/eob_ocr_tool.py` | AWS Textract (swap `extract_text_from_pdf()`) |
| SOP Knowledge Base | `tools/sop_rag_tool.py` | ChromaDB + OpenAI Embeddings |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Tesseract OCR: `sudo apt install tesseract-ocr poppler-utils` (Linux) or via Homebrew (Mac)

### 2. Install

```bash
git clone <repo>
cd rcm_denial_management

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — set OPENAI_API_KEY at minimum
```

### 4. Seed the Knowledge Base (requires OpenAI key)

```bash
python scripts/seed_knowledge_base.py
# or:
rcm-denial seed-kb
```

### 5. Process a Batch

```bash
rcm-denial process-batch data/sample_denials.csv
```

### 6. Process a Single Claim

```bash
rcm-denial process-claim \
  --claim-id CLM-001 \
  --patient-id PAT001 \
  --payer-id BCBS \
  --provider-id PROV-NPI-001 \
  --dos 2024-09-20 \
  --cpt 27447 \
  --dx M17.11 \
  --denial-reason "Prior authorization not obtained" \
  --carc 97 \
  --rarc N4 \
  --denial-date 2024-10-05 \
  --amount 15000
```

### 7. Run Tests

```bash
pytest tests/ -v
# or:
rcm-denial run-tests --verbose
```

---

## Project Structure

```
rcm_denial_management/
├── main.py                          # CLI entry point + programmatic API
├── pyproject.toml                   # Dependencies
├── .env.example                     # Environment template
├── config/
│   └── settings.py                  # pydantic-settings config
├── models/
│   ├── claim.py                     # ClaimRecord, EnrichedData, PatientData...
│   ├── analysis.py                  # DenialAnalysis, CorrectionPlan
│   ├── appeal.py                    # AppealPackage, AppealLetter
│   └── output.py                    # DenialWorkflowState, SubmissionPackage, BatchReport
├── agents/
│   ├── intake_agent.py
│   ├── enrichment_agent.py
│   ├── analysis_agent.py
│   ├── correction_plan_agent.py
│   ├── appeal_prep_agent.py
│   └── document_packaging_agent.py
├── tools/
│   ├── patient_data_tool.py         # Mock → FHIR R4 integration
│   ├── payer_policy_tool.py         # Mock → Contract management
│   ├── ehr_tool.py                  # Mock → Epic/Cerner FHIR
│   ├── eob_ocr_tool.py              # pytesseract → AWS Textract
│   └── sop_rag_tool.py              # ChromaDB RAG + keyword fallback
├── workflows/
│   ├── denial_graph.py              # LangGraph StateGraph (main graph)
│   ├── supervisor_router.py         # Conditional edge routing logic
│   └── batch_processor.py          # Sequential batch engine
├── services/
│   ├── audit_service.py             # structlog configuration
│   └── pdf_service.py               # reportlab PDF generation + pypdf merge
├── data/
│   ├── sample_denials.csv           # 5 sample denied claims
│   ├── carc_rarc_reference.json     # CARC/RARC code reference
│   └── sop_documents/               # SOP text files for RAG seeding
├── evaluation/
│   ├── evaluator.py                 # 5-metric evaluation framework
│   └── test_cases.json              # Ground truth for evaluation
├── scripts/
│   └── seed_knowledge_base.py       # ChromaDB seeding script
└── tests/
    ├── test_agents.py
    ├── test_tools.py
    └── test_batch_processor.py
```

---

## Programmatic Integration

Plug into any existing Python application with minimal changes:

```python
# Single claim
from main import process_claim_api

result = process_claim_api({
    "claim_id": "CLM-001",
    "patient_id": "PAT001",
    "payer_id": "BCBS",
    "provider_id": "PROV-NPI-001",
    "date_of_service": "2024-09-20",
    "cpt_codes": "27447",
    "diagnosis_codes": "M17.11",
    "denial_reason": "Prior authorization not obtained",
    "carc_code": "97",
    "rarc_code": "N4",
    "denial_date": "2024-10-05",
    "billed_amount": 15000.00,
})
print(result["status"])         # "complete"
print(result["package_type"])   # "appeal"
print(result["pdf_package_path"])

# Batch
from main import process_batch_api

report = process_batch_api("data/claims.csv")
print(f"Success rate: {report['success_rate']}%")
```

---

## Output Structure

For each processed claim, the system produces:

```
output/
└── CLM-2024-001/
    ├── 01_denial_analysis.pdf          # Analysis report
    ├── 02_correction_plan.pdf          # Correction plan (resubmit path)
    ├── 03_appeal_letter.pdf            # Formal appeal letter (appeal path)
    ├── SUBMISSION_PACKAGE_CLM-2024-001.pdf  # Merged final package
    ├── submission_metadata.json        # Machine-readable claim metadata
    └── audit_log.json                  # Immutable processing audit trail
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for LLM analysis and embeddings |
| `OPENAI_MODEL` | `gpt-4o` | LLM model for analysis and appeal generation |
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith tracing |
| `CHROMA_PERSIST_DIR` | `./data/chroma_db` | ChromaDB storage path |
| `SKIP_COMPLETED_CLAIMS` | `true` | Idempotency — skip re-processing completed claims |
| `BATCH_MAX_RETRIES` | `3` | LLM call retry attempts |
| `OUTPUT_DIR` | `./output` | Where submission packages are written |

---

## Running Without OpenAI Key

The system works fully in **offline/mock mode** without an API key:
- Analysis agent uses **rule-based fallback** routing (CARC → action mapping)
- Enrichment uses **mock data** from all five tool modules
- SOP retrieval uses **keyword-based matching** (no vector embeddings needed)
- PDF generation works with **reportlab** (no LLM needed)

Only the LLM-generated appeal letter quality will be lower in mock mode.

---

## Evaluation

```bash
# After processing the sample batch:
python evaluation/evaluator.py
```

Produces `output/evaluation_report.json` with 5 metrics per claim:
1. **Classification Accuracy** (30%) — action + category correctness
2. **CARC Interpretation** (20%) — keyword overlap with reference descriptions
3. **Document Completeness** (20%) — required files present in output
4. **Appeal Letter Quality** (20%) — LLM-as-judge 0-5 score
5. **Latency** (10%) — processing time vs. thresholds

---

## Future Enhancements

- **Parallel batch processing** — replace sequential loop with `asyncio.gather` + `Semaphore`
- **Real API integrations** — swap mock tools with FHIR / Availity / Epic endpoints
- **AWS Textract** — replace pytesseract in `eob_ocr_tool.py`
- **REST API wrapper** — FastAPI layer over `process_claim_api()`
- **Payer portal automation** — Selenium/Playwright for portal submission
- **Human-in-the-loop** — LangGraph interrupt nodes for reviewer approval on high-value claims
