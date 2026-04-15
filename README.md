# RCM Denial Management — Agentic AI System

**Project:** RCM - Denial Management
**Author:** RK (kvrkr866@gmail.com)
**Version:** 1.0.0

A production-grade, multi-agent AI system built with **LangGraph** that automates medical claim denial management for USA hospitals. The system analyzes denied claims using CARC/RARC codes, enriches them with clinical and payer data, generates resubmission or appeal packages, routes through human review, submits to payer portals, and tracks cost/quality metrics end-to-end.

---

## Table of Contents

1. [Features at a Glance](#features-at-a-glance)
2. [Deployment and Setup](#deployment-and-setup)
3. [Running the System — End-to-End Workflow](#running-the-system--end-to-end-workflow)
4. [Complete CLI Reference (All Commands and Options)](#complete-cli-reference-all-commands-and-options)
5. [Architecture Overview](#architecture-overview)
6. [Project Structure](#project-structure)
7. [Programmatic API](#programmatic-api)
8. [Configuration Reference](#configuration-reference)
9. [Test Suite](#test-suite)
10. [Future Enhancements](#future-enhancements)

---

## Features at a Glance

### Phase 1 — Core AI Pipeline
- **10-agent LangGraph pipeline** — intake, enrichment, analysis, evidence check, correction planning, appeal generation, document packaging
- **LLM-powered analysis** — CARC/RARC root cause analysis with structured Pydantic output (gpt-4o / gpt-4o-mini)
- **Rule-based fallback** — every LLM agent has a deterministic CARC-map fallback; system works fully offline without an OpenAI key
- **5-source data enrichment** — patient data, payer policy, provider EHR, EOB OCR, SOP knowledge base (all run in parallel via asyncio)
- **Supervisor routing** — automatically routes claims to resubmit, appeal, both, or write-off paths
- **PDF generation** — analysis report, correction plan, appeal letter, merged submission package (reportlab + pypdf)
- **Structured output** — `submission_metadata.json` + `audit_log.json` per claim
- **Batch processing** — CSV-driven with idempotency (skips already-completed claims)

### Phase 2 — Evidence Check and Targeted EHR
- **Evidence check agent** — LLM call 1: assesses whether available evidence is sufficient before generating a response
- **Targeted EHR fetch** — Stage 2 EHR retrieval for lab results, imaging studies, pathology reports when evidence is insufficient
- **Response agent** — LLM call 2: generates CorrectionPlan and/or AppealLetter informed by evidence assessment

### Phase 3 — Per-Payer SOP RAG Knowledge Base
- **Per-payer ChromaDB collections** — `sop_bcbs`, `sop_aetna`, etc. built from folder-based payer discovery
- **Manifest tracking** — `manifest.json` records collection health: document count, index timestamp, verification status
- **Pipeline mode** — SOP indexing is blocked during batch runs; missing collections fall back to keyword search
- **Pre-flight check** — verifies payer coverage against manifest before batch starts
- **`rcm-denial init`** — one-command SOP setup with `--verify` to test-query after indexing

### Phase 4 — Human-in-the-Loop Review Queue
- **Non-blocking review** — every claim completes the full AI pipeline, then enters the review queue
- **AI-generated summary** — decision-ready summary with root cause, evidence confidence, key arguments, flag reasons
- **4 reviewer actions** — approve, re-route (to any pipeline stage), human override, write-off
- **Write-off guard** — blocks write-offs unless re-route was attempted first (prevents premature revenue loss)
- **Bulk approve** — auto-approve low-risk claims by confidence and amount thresholds
- **Pipeline re-entry** — re-routed claims re-run from the chosen stage with reviewer notes injected into LLM prompts

### Phase 5 — Payer Submission
- **4 submission adapters** — MockSubmissionAdapter (dev/test), AvailitySubmissionAdapter (REST API scaffold), RPASubmissionAdapter (Playwright portal scaffold), EDI837SubmissionAdapter (EDI transaction scaffold)
- **Per-payer registry** — `payer_submission_registry` table maps each payer to its submission method
- **Retry with exponential backoff** — tenacity-based retry for transient network errors
- **Submission log** — every attempt (success or failure) logged to `submission_log` table
- **Status checking** — poll payer for adjudication status after submission

### Phase 6 — Observability, Cost Tracking, Database Backend
- **LLM cost tracking** — records model, input/output tokens, USD cost per LLM call; per-claim and per-batch summaries
- **Prometheus metrics** — 9 metric families exported to `data/metrics/rcm_denial.prom` (textfile collector pattern)
- **Pushgateway support** — optional push after each batch for real-time monitoring
- **Pre-built Grafana dashboard** — 6 panels: pipeline overview, duration percentiles, LLM cost, review queue, submissions, write-off impact
- **Loki log aggregation** — Promtail ships structured JSON logs to Loki with claim_id/batch_id labels
- **Docker Compose stack** — one-command `docker compose up` for Prometheus + Grafana + Loki + Pushgateway
- **PostgreSQL backend** — production database option; one-command migration from SQLite
- **Comprehensive `stats` command** — single-pane scorecard with pipeline results, LLM cost, review queue, submissions, write-offs, eval quality signals

### Evaluation System
- **Continuous eval from review queue** — reviewer actions = quality ground truth (first-pass approval rate, override rate, confidence calibration)
- **Deterministic criteria checks** — 22 structural assertions on LLM output (no LLM cost, always deterministic)
- **Golden dataset** — 14 labeled cases covering all 7 denial categories and all 4 actions for regression testing
- **LLM-as-judge evaluation** — 5-metric composite scoring framework

---

## Deployment and Setup

### Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python | 3.11+ | Runtime |
| pip | latest | Package manager |
| Tesseract OCR | any | EOB PDF text extraction (optional) |
| poppler-utils | any | PDF-to-image conversion for OCR (optional) |
| Docker + Docker Compose | any | Observability stack — Grafana/Prometheus/Loki (optional) |
| PostgreSQL | 14+ | Production database backend (optional; SQLite is default) |

### Step 1 — Install System Dependencies

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip tesseract-ocr poppler-utils
```

**macOS (Homebrew):**
```bash
brew install python@3.11 tesseract poppler
```

**Windows (WSL recommended):**
```bash
# Inside WSL Ubuntu:
sudo apt install -y python3.11 python3.11-venv python3-pip tesseract-ocr poppler-utils
```

### Step 2 — Clone and Create Virtual Environment

```bash
git clone <repo-url>
cd rcm_denial_proto

python3 -m venv .venv
source .venv/bin/activate          # Windows WSL: same command
                                   # Windows CMD: .venv\Scripts\activate
```

### Step 3 — Install Python Dependencies

```bash
# Install the package in editable mode with dev dependencies
pip install -e ".[dev]"
```

This installs all required packages including:
- `langgraph`, `langchain`, `langchain-openai`, `langchain-chroma` — AI pipeline
- `chromadb` — vector database for SOP RAG
- `pydantic`, `pydantic-settings` — data models and configuration
- `structlog` — structured logging
- `tenacity` — retry logic
- `reportlab`, `pypdf` — PDF generation
- `pytesseract`, `pdf2image`, `Pillow` — OCR
- `rich`, `click` — CLI interface
- `pytest`, `ruff`, `mypy` — dev tools

### Step 4 — Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
```dotenv
OPENAI_API_KEY=sk-your-key-here    # Required for LLM features
```

All other variables have sensible defaults. See [Configuration Reference](#configuration-reference) for the full list.

**Running without an OpenAI key:** The system works fully in offline/mock mode. Analysis uses rule-based CARC-map fallback, enrichment uses mock data, SOP uses keyword matching, and PDF generation works without LLM. Only appeal letter quality is reduced.

### Step 5 — Initialize SOP Knowledge Base

Place SOP documents in per-payer folders:
```
data/sop_documents/
├── global/          # SOPs applying to all payers
│   ├── timely_filing_sop.txt
│   └── general_appeal_guidelines.txt
├── bcbs/            # BCBS-specific SOPs
│   ├── bcbs_appeal_process.txt
│   └── bcbs_prior_auth_requirements.txt
├── aetna/           # Aetna-specific SOPs
│   └── aetna_medical_necessity_criteria.txt
└── medicare/        # Medicare-specific SOPs
    └── medicare_lcd_guidelines.txt
```

Then build the RAG collections:
```bash
rcm-denial init                    # Discover all payer folders and build collections
rcm-denial init --verify           # Build + run test queries to confirm health
rcm-denial init --payer BCBS       # Build/refresh a single payer's collection
rcm-denial init --check-only       # Report coverage without indexing
```

### Step 6 — Verify Installation

```bash
# Check CLI is working
rcm-denial --help

# Run tests (104 tests should pass)
pytest tests/ -q

# Check database backend
rcm-denial db info

# Check SOP coverage
rcm-denial sop-status

# Run eval self-consistency check
rcm-denial evals run
```

### Step 7 — (Optional) Start Observability Stack

```bash
docker compose -f data/observability/docker-compose.yml up -d
```

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |
| Pushgateway | http://localhost:9091 | — |
| Loki | http://localhost:3100 | — |

Grafana auto-provisions the Prometheus datasource and the RCM Denial dashboard on startup.

### Step 8 — (Optional) Switch to PostgreSQL for Production

```bash
# 1. Set in .env:
DATABASE_TYPE=postgresql
DATABASE_URL=postgresql://user:password@host:5432/rcm_denial

# 2. Create schema on the target server:
rcm-denial db export-schema | psql -d rcm_denial

# 3. One-time data migration from SQLite:
rcm-denial db migrate-to-postgres

# 4. Verify:
rcm-denial db info
```

---

## Running the System — End-to-End Workflow

### Typical Workflow

```
 Step 1: Process denied claims CSV
    │
    ▼
 Step 2: AI pipeline runs (intake → enrich → analyze → evidence check → response → package)
    │
    ▼
 Step 3: Claims land in review queue
    │
    ▼
 Step 4: Human reviewer approves / re-routes / overrides / writes off
    │
    ▼
 Step 5: Submit approved claims to payer portals
    │
    ▼
 Step 6: View statistics and eval quality signals
```

### Step 1 — Process a Batch of Denied Claims

```bash
# Process the sample data (5 claims)
rcm-denial process-batch data/sample_denials.csv

# With a named batch ID for tracking
rcm-denial process-batch data/sample_denials.csv --batch-id BATCH-2024-Q4-001

# Re-process already completed claims (override idempotency)
rcm-denial process-batch data/sample_denials.csv --no-skip

# Limit to specific payer for testing
rcm-denial process-batch data/sample_denials.csv --source-label "Q4 BCBS batch"
```

Or process a single claim interactively:
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
  --amount 15000 \
  --eob-path data/eob_pdfs/CLM-001.pdf
```

Output per claim:
```
output/CLM-001/
├── 01_denial_analysis.pdf              # Root cause analysis report
├── 02_correction_plan.pdf              # Code corrections + documentation checklist
├── 03_appeal_letter.pdf                # Formal appeal with clinical justification
├── SUBMISSION_PACKAGE_CLM-001.pdf      # Merged final package
├── submission_metadata.json            # Machine-readable metadata
└── audit_log.json                      # Immutable audit trail
```

### Step 2 — Review Intake Results

```bash
# View validation results for the batch
rcm-denial intake-report --batch-id BATCH-2024-Q4-001

# Filter by source CSV
rcm-denial intake-report --csv data/sample_denials.csv
```

### Step 3 — Human Review

```bash
# List claims pending review (sorted by urgency and amount)
rcm-denial review list --status pending

# List all review queue items
rcm-denial review list

# Filter by batch
rcm-denial review list --batch-id BATCH-2024-Q4-001 --limit 100

# View full detail for one claim (includes AI summary)
rcm-denial review detail --run-id <run_id>
```

### Step 4 — Take Reviewer Action

```bash
# APPROVE — claim is ready for payer submission
rcm-denial review approve --run-id <run_id> --reviewer "billing_mgr"

# BULK APPROVE — auto-approve low-risk claims
rcm-denial review bulk-approve \
  --confidence-above 0.85 \
  --amount-below 5000 \
  --batch-id BATCH-2024-Q4-001

# RE-ROUTE — re-run pipeline from a specific stage
rcm-denial review re-route --run-id <run_id> \
  --stage response_agent \
  --notes "Include physical therapy records from last 6 months" \
  --execute

# Valid re-entry stages:
#   intake_agent         — re-process from scratch (claim data was wrong)
#   targeted_ehr_agent   — fetch more clinical evidence
#   response_agent       — regenerate letter/plan with reviewer guidance

# HUMAN OVERRIDE — replace AI output with reviewer-written text
rcm-denial review override --run-id <run_id> \
  --text "Dear Appeals Department: We formally contest this denial..." \
  --execute

# WRITE-OFF — last resort (blocked unless re-route attempted first)
rcm-denial review write-off --run-id <run_id> \
  --reason cost_exceeds_recovery \
  --notes "Balance of $95 is below collection cost threshold" \
  --force        # manager override to bypass re-route requirement

# Valid write-off reasons:
#   timely_filing_expired    — exempt from re-route guard
#   cost_exceeds_recovery
#   payer_non_negotiable
#   duplicate_confirmed_paid
#   patient_responsibility
#   other

# EXECUTE RE-ENTRY — after re-route or override, run the pipeline re-entry
rcm-denial review execute-reentry --run-id <run_id>
```

### Step 5 — Submit to Payer

```bash
# Submit one approved claim
rcm-denial submit --run-id <run_id>

# Dry run (validate without submitting)
rcm-denial submit --run-id <run_id> --dry-run

# Submit all approved claims in a batch
rcm-denial submit-batch --batch-id BATCH-2024-Q4-001

# Dry run batch
rcm-denial submit-batch --batch-id BATCH-2024-Q4-001 --dry-run

# Check adjudication status after submission
rcm-denial submission-status --run-id <run_id>

# View submission attempt history (including retries)
rcm-denial submission-log --run-id <run_id>
```

### Step 6 — View Statistics and Quality

```bash
# Comprehensive scorecard (pipeline + LLM cost + review queue + submissions + write-offs + eval signals)
rcm-denial stats

# Stats for a specific batch
rcm-denial stats --batch-id BATCH-2024-Q4-001

# Export Prometheus-format metrics file
rcm-denial stats --export-metrics

# Push to Prometheus Pushgateway
rcm-denial stats --push-gateway http://localhost:9091

# Submission success/failure summary
rcm-denial submission-stats
rcm-denial submission-stats --batch-id BATCH-2024-Q4-001

# Eval quality signals from review queue
rcm-denial evals quality-signals

# Run golden dataset regression checks
rcm-denial evals run

# Check against actual pipeline output
rcm-denial evals run --output-dir ./output --json-out data/evals/report.json

# Criteria check on a single claim
rcm-denial evals check-output CLM-001
```

---

## Complete CLI Reference (All Commands and Options)

### `rcm-denial process-batch`

Process a CSV of denied claims through the full AI pipeline.

```
Usage: rcm-denial process-batch [OPTIONS] CSV_PATH

Options:
  --batch-id TEXT       Optional batch identifier (auto-generated if empty)
  --no-skip             Re-process already completed claims (override idempotency)
  --source-label TEXT   Human-readable label for this CSV source
  --output-dir TEXT     Override output directory (default: ./output)
```

### `rcm-denial process-claim`

Process a single claim interactively.

```
Usage: rcm-denial process-claim [OPTIONS]

Options:
  --claim-id TEXT        Claim ID [required]
  --patient-id TEXT      Patient ID [required]
  --payer-id TEXT        Payer ID [required]
  --provider-id TEXT     Provider ID [required]
  --dos TEXT             Date of service (YYYY-MM-DD) [required]
  --cpt TEXT             CPT codes (comma-separated) [required]
  --dx TEXT              Diagnosis codes (comma-separated) [required]
  --denial-reason TEXT   Denial reason text [required]
  --carc TEXT            CARC code [required]
  --rarc TEXT            RARC code (optional)
  --denial-date TEXT     Denial date (YYYY-MM-DD) [required]
  --amount FLOAT         Billed amount (USD) [required]
  --eob-path TEXT        Path to EOB PDF (optional)
```

### `rcm-denial init`

Initialize SOP knowledge base — discover payer folders and build ChromaDB RAG collections.

```
Usage: rcm-denial init [OPTIONS]

Options:
  --payer TEXT     Build/refresh only this payer (e.g. BCBS). Omit for all payers.
  --check-only     Report payer coverage without indexing
  --strict         Fail on missing payer collections (for CI/CD)
  --verify         Run test queries after indexing to confirm health
```

### `rcm-denial ingest-sop`

Ingest SOP documents for a specific payer.

```
Usage: rcm-denial ingest-sop [OPTIONS]

Options:
  --payer TEXT     Payer ID (e.g. BCBS) [required]
  --dir TEXT       Directory of SOP documents [required]
  --verify         Run verification query after ingestion
```

### `rcm-denial seed-kb`

Seed the global ChromaDB collection (legacy).

### `rcm-denial sop-status`

Show SOP collection stats and manifest health for all payers.

### `rcm-denial intake-report`

View CSV validation results.

```
Usage: rcm-denial intake-report [OPTIONS]

Options:
  --batch-id TEXT   Filter by batch ID
  --csv TEXT        Filter by source CSV path
```

### `rcm-denial review list`

List review queue items.

```
Usage: rcm-denial review list [OPTIONS]

Options:
  --batch-id TEXT     Filter by batch ID
  --status TEXT       Filter by status (pending|approved|re_routed|re_processed|
                      human_override|written_off|submitted)
  --limit INTEGER     Max rows to display (default: 50)
```

### `rcm-denial review detail`

Show full detail for a queue item including AI summary.

```
Usage: rcm-denial review detail [OPTIONS]

Options:
  --run-id TEXT   Run ID of the claim [required]
```

### `rcm-denial review approve`

Approve a claim for payer submission.

```
Usage: rcm-denial review approve [OPTIONS]

Options:
  --run-id TEXT      Run ID of the claim [required]
  --reviewer TEXT    Reviewer identifier (default: human)
```

### `rcm-denial review bulk-approve`

Auto-approve low-risk pending claims.

```
Usage: rcm-denial review bulk-approve [OPTIONS]

Options:
  --batch-id TEXT             Limit to a specific batch
  --confidence-above FLOAT   Min confidence score (default: 0.85)
  --amount-below FLOAT       Max billed amount (default: 5000.0)
  --reviewer TEXT             Reviewer identifier (default: human)
```

### `rcm-denial review re-route`

Re-route a claim back into the pipeline at a specific stage.

```
Usage: rcm-denial review re-route [OPTIONS]

Options:
  --run-id TEXT     Run ID of the claim [required]
  --stage TEXT      Re-entry stage: intake_agent | targeted_ehr_agent | response_agent [required]
  --notes TEXT      Guidance injected into re-run LLM prompts
  --reviewer TEXT   Reviewer identifier (default: human)
  --execute         Execute re-entry immediately after marking
```

### `rcm-denial review override`

Replace AI output with reviewer-written text.

```
Usage: rcm-denial review override [OPTIONS]

Options:
  --run-id TEXT     Run ID of the claim [required]
  --text TEXT       Reviewer-written response text [required]
  --reviewer TEXT   Reviewer identifier (default: human)
  --execute         Execute re-packaging immediately after override
```

### `rcm-denial review execute-reentry`

Execute pipeline re-entry for a re-routed or overridden claim.

```
Usage: rcm-denial review execute-reentry [OPTIONS]

Options:
  --run-id TEXT   Run ID of a re_routed or human_override claim [required]
```

### `rcm-denial review write-off`

Mark a claim as written off (last resort).

```
Usage: rcm-denial review write-off [OPTIONS]

Options:
  --run-id TEXT     Run ID of the claim [required]
  --reason TEXT     Write-off reason: timely_filing_expired | cost_exceeds_recovery |
                    payer_non_negotiable | duplicate_confirmed_paid |
                    patient_responsibility | other [required]
  --notes TEXT      Additional write-off justification notes
  --reviewer TEXT   Reviewer identifier (default: human)
  --force           Bypass the re-route guard (manager override)
```

### `rcm-denial review stats`

Review queue summary with write-off revenue impact.

```
Usage: rcm-denial review stats [OPTIONS]

Options:
  --batch-id TEXT   Filter by batch ID
```

### `rcm-denial submit`

Submit one approved claim to payer.

```
Usage: rcm-denial submit [OPTIONS]

Options:
  --run-id TEXT   Run ID of the approved claim [required]
  --dry-run       Validate without actually submitting
```

### `rcm-denial submit-batch`

Submit all approved claims in a batch.

```
Usage: rcm-denial submit-batch [OPTIONS]

Options:
  --batch-id TEXT   Batch ID [required]
  --dry-run         Validate without actually submitting
```

### `rcm-denial submission-status`

Check adjudication status of a submitted claim.

```
Usage: rcm-denial submission-status [OPTIONS]

Options:
  --run-id TEXT   Run ID of a submitted claim [required]
```

### `rcm-denial submission-log`

View submission attempt history.

```
Usage: rcm-denial submission-log [OPTIONS]

Options:
  --run-id TEXT   Run ID to show submission attempts for [required]
```

### `rcm-denial submission-registry list`

List all registered payer submission methods.

### `rcm-denial submission-registry register`

Register or update a payer's submission method.

```
Usage: rcm-denial submission-registry register [OPTIONS]

Options:
  --payer-id TEXT        Payer ID (e.g. BCBS) [required]
  --method TEXT          Submission method: mock | availity_api | rpa_portal | edi_837 [required]
  --api-endpoint TEXT    REST API base URL (for availity_api)
  --portal-url TEXT      Web portal URL (for rpa_portal)
  --clearinghouse TEXT   Clearinghouse ID (for edi_837)
  --notes TEXT           Free-text notes
```

### `rcm-denial submission-stats`

Submission success/failure summary.

```
Usage: rcm-denial submission-stats [OPTIONS]

Options:
  --batch-id TEXT   Filter by batch ID
```

### `rcm-denial stats`

Comprehensive pipeline scorecard — the single-pane view.

```
Usage: rcm-denial stats [OPTIONS]

Options:
  --batch-id TEXT       Filter by batch ID (default: all-time)
  --export-metrics      Write data/metrics/rcm_denial.prom
  --push-gateway TEXT   Push metrics to Prometheus Pushgateway URL
```

Displays: pipeline success/failure, processing duration percentiles (p50/p95/p99), LLM cost breakdown by model, review queue status breakdown, submission outcomes, write-off revenue impact, and eval quality signals (first-pass approval rate, override rate, confidence calibration, re-route hotspots).

### `rcm-denial db info`

Show current database backend and connection details.

### `rcm-denial db export-schema`

Print the PostgreSQL DDL schema.

```
Usage: rcm-denial db export-schema [OPTIONS]

Options:
  --output TEXT   Write to file instead of stdout
```

### `rcm-denial db migrate-to-postgres`

One-time migration: copy all SQLite data to PostgreSQL.

### `rcm-denial evals run`

Run golden dataset regression checks.

```
Usage: rcm-denial evals run [OPTIONS]

Options:
  --golden-cases TEXT   Path to golden_cases.json (default: data/evals/golden_cases.json)
  --output-dir TEXT     Pipeline output dir to check actual LLM outputs (optional)
  --json-out TEXT       Write full report as JSON to this file (optional)
```

### `rcm-denial evals check-output`

Run criteria checks on a single claim's pipeline output.

```
Usage: rcm-denial evals check-output [OPTIONS] CLAIM_ID

Options:
  --output-dir TEXT   Pipeline output base directory (default: ./output)
```

### `rcm-denial evals quality-signals`

Show review-queue quality signals (continuous eval from reviewer actions).

### `rcm-denial run-tests`

Run the test suite.

```
Usage: rcm-denial run-tests [OPTIONS]

Options:
  -v, --verbose   Verbose output
```

---

## Architecture Overview

```
                          ┌──────────────────────────────────────────┐
                          │          LangGraph StateGraph            │
                          │                                          │
CSV Input ──► Intake ──► Enrichment ──► Analysis ──► Evidence Check  │
                          │  (5 tools)      │            │           │
                          │                 │     [Supervisor Router] │
                          │          ┌──────┴──────────┐             │
                          │   Correction Plan     Appeal Prep        │
                          │          └──────┬──────────┘             │
                          │        Document Packaging                │
                          │               │                          │
                          └───────────────┼──────────────────────────┘
                                          ▼
                                   Review Gate Agent
                                          │
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                          Approve    Re-route    Write-off
                              │       (back to      │
                              ▼      any stage)     ▼
                     Payer Submission            Revenue
                     (mock/API/RPA/EDI)         Impact Log
                              │
                              ▼
                  Prometheus Metrics + Grafana
```

### Pipeline Agents

| Agent | Node Name | Responsibility |
|-------|-----------|----------------|
| Intake | `intake_agent` | Validate and parse ClaimRecord from CSV row |
| Enrichment | `enrichment_agent` | Async fan-out to 5 data sources via asyncio.gather |
| Analysis | `analysis_agent` | LLM structured output → DenialAnalysis; rule-based CARC-map fallback |
| Evidence Check | `evidence_check_agent` | LLM call 1: evidence sufficiency, key arguments, gaps; cost tracked |
| Targeted EHR | `targeted_ehr_agent` | Stage 2 fetch: labs, imaging, pathology from EHR when evidence is insufficient |
| Response | `response_agent` | LLM call 2: CorrectionPlan and/or AppealLetter; cost tracked |
| Correction Plan | `correction_plan_agent` | Code corrections + documentation checklist; rule-based fallback |
| Appeal Prep | `appeal_prep_agent` | Formal appeal letter with clinical justification + regulatory basis |
| Packaging | `document_packaging_agent` | PDF generation (reportlab), merge (pypdf), metadata JSON, audit trail |
| Review Gate | `review_gate_agent` | Enqueue completed claim with AI summary for human review |

### External System Adapters

| System | Env Var | Options |
|--------|---------|---------|
| EMR/EHR | `EMR_ADAPTER` | `mock` \| `epic` \| `cerner` \| `athena` \| `rpa_portal` |
| Practice Management | `PMS_ADAPTER` | `mock` \| `kareo` \| `advancedmd` |
| Payer Data | `PAYER_ADAPTER` | `mock` \| `availity` \| `change_healthcare` |
| Payer Submission | `SUBMISSION_ADAPTER` | `mock` \| `availity_api` \| `rpa_portal` \| `edi_837` |

### Denial Categories Handled

| Category | CARC Codes | Typical Action |
|----------|-----------|----------------|
| Timely filing | 29, 119, 181 | Resubmit with proof of timely submission |
| Medical necessity | 50, 57, 151, 167, 197 | Appeal with clinical documentation |
| Prior authorization | 15, 197, 278 | Appeal with retrospective auth request |
| Duplicate claim | 18, 97 | Resubmit as replacement (bill type 7) |
| Coding error | 4, 5, 6, 11, 16, 22, 96 | Resubmit with corrected codes |
| Eligibility | 27, 31, 181 | Resubmit with eligibility verification |
| Coordination of benefits | 22, 23, 24 | Both (resubmit to correct primary + appeal) |

---

## Project Structure

```
rcm_denial_proto/
├── pyproject.toml                          # Dependencies, entry point: rcm_denial.main:cli
├── .env.example                            # Full environment variable template (40+ vars)
│
├── src/rcm_denial/
│   ├── main.py                             # CLI (click) — 30+ commands + programmatic API
│   ├── config/settings.py                  # Pydantic-settings singleton (all env vars)
│   ├── models/                             # Pydantic v2 data models
│   │   ├── claim.py                        # ClaimRecord, EnrichedData, PatientData, PayerPolicy,
│   │   │                                   #   EhrData, DiagnosticReport, EobExtractedData, SopResult
│   │   ├── analysis.py                     # DenialAnalysis, EvidenceCheckResult, CorrectionPlan
│   │   ├── appeal.py                       # AppealPackage, AppealLetter, SupportingDocument
│   │   ├── output.py                       # DenialWorkflowState, SubmissionPackage, AuditEntry
│   │   └── submission.py                   # SubmissionResult, SubmissionStatus
│   ├── agents/                             # 10 LangGraph agent nodes
│   ├── tools/                              # 6 data source tools (mock → real integration)
│   ├── workflows/                          # LangGraph StateGraph, supervisor router, batch engine
│   ├── services/                           # 13 service modules (DB, queue, submission, metrics, etc.)
│   ├── evaluation/evaluator.py             # 5-metric LLM-as-judge evaluation
│   └── evals/criteria_checks.py            # 22 deterministic structural assertions
│
├── data/
│   ├── sample_denials.csv                  # 5 sample denied claims
│   ├── carc_rarc_reference.json            # CARC/RARC code reference
│   ├── sop_documents/                      # Per-payer SOP folders + manifest.json
│   ├── evals/golden_cases.json             # 14 labeled golden cases
│   └── observability/                      # Docker Compose + Grafana + Prometheus + Loki configs
│
├── tests/                                  # 8 test files, 104+ tests
│   ├── conftest.py                         # Shared fixtures (DB isolation, SOP isolation)
│   ├── test_review_queue.py                # 15 tests — queue actions, write-off guard, eval stats
│   ├── test_submission.py                  # 14 tests — adapters, registry, retry logic
│   ├── test_cost_tracker.py                # 17 tests — cost calculation, recording, summaries
│   ├── test_sop_pipeline_mode.py           # 17 tests — pipeline mode, manifest, coverage
│   └── test_criteria_checks.py             # 24 tests — structural assertions, golden dataset
│
├── output/                                 # Generated submission packages (per claim)
└── logs/                                   # Structured JSON logs (structlog)
```

---

## Programmatic API

Integrate into any existing Python application:

```python
# Single claim
from rcm_denial.main import process_claim_api

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
print(result["status"])           # "complete"
print(result["package_type"])     # "appeal"
print(result["pdf_package_path"])

# Batch
from rcm_denial.main import process_batch_api

report = process_batch_api("data/claims.csv")
print(f"Success rate: {report['success_rate']}%")
```

---

## Configuration Reference

All variables with defaults. See `.env.example` for full documentation.

### OpenAI / LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for LLM features |
| `OPENAI_MODEL` | `gpt-4o` | LLM model for analysis and response generation |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model for SOP RAG |
| `OPENAI_MAX_TOKENS` | `4096` | Max output tokens per LLM call |
| `OPENAI_TEMPERATURE` | `0.1` | LLM temperature (0.0 = deterministic) |

### LangSmith Tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGCHAIN_TRACING_V2` | `false` | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | — | LangSmith API key |
| `LANGCHAIN_PROJECT` | `rcm-denial-management` | LangSmith project name |

### ChromaDB / SOP RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_PERSIST_DIR` | `./data/chroma_db` | ChromaDB storage path |
| `CHROMA_COLLECTION_NAME` | `sop_global` | Fallback collection name |
| `SOP_RAG_FRESHNESS_CHECK` | `true` | Re-index when SOP files are newer |
| `SOP_MIN_RELEVANCE_SCORE` | `0.3` | Minimum similarity score for RAG results |
| `SOP_PIPELINE_STRICT_MODE` | `false` | Fail batch on missing SOP collections |

### External Adapters

| Variable | Default | Description |
|----------|---------|-------------|
| `EMR_ADAPTER` | `mock` | EMR adapter: mock \| epic \| cerner \| athena \| rpa_portal |
| `PMS_ADAPTER` | `mock` | PMS adapter: mock \| kareo \| advancedmd |
| `PAYER_ADAPTER` | `mock` | Payer adapter: mock \| availity \| change_healthcare |
| `SUBMISSION_ADAPTER` | `mock` | Default submission: mock \| availity_api \| rpa_portal \| edi_837 |

### Submission Retry

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBMISSION_MAX_RETRIES` | `3` | Retry attempts for transient network failures |
| `SUBMISSION_RETRY_DELAY_SECONDS` | `5.0` | Initial backoff delay (exponential: x2 each retry) |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_TYPE` | `sqlite` | Backend: sqlite \| postgresql |
| `DATABASE_URL` | — | PostgreSQL URL: `postgresql://user:pass@host:5432/db` |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `METRICS_EXPORT_AFTER_BATCH` | `true` | Auto-write .prom file after each batch |
| `PROMETHEUS_PUSHGATEWAY_URL` | — | Pushgateway URL (empty = disabled) |

### Batch Processing

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_COMPLETED_CLAIMS` | `true` | Idempotency — skip already-processed claims |
| `MAX_CONCURRENT_CLAIMS` | `1` | Max concurrent claims (future use) |
| `BATCH_MAX_RETRIES` | `3` | LLM call retry attempts within pipeline |
| `BATCH_RETRY_DELAY_SECONDS` | `2.0` | Initial retry delay within pipeline |

### Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `OUTPUT_DIR` | `./output` | Submission package output directory |
| `LOG_DIR` | `./logs` | Structured log files directory |
| `DATA_DIR` | `./data` | Data directory (DB, metrics, SOPs) |
| `LOG_LEVEL` | `INFO` | Logging verbosity: DEBUG \| INFO \| WARNING \| ERROR |

### OCR (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERACT_CMD` | `/usr/bin/tesseract` | Path to Tesseract binary |
| `OCR_DPI` | `300` | OCR resolution for PDF scanning |

---

## Test Suite

```bash
# Run all tests
pytest tests/ -v

# Run only phase 2-6 tests (skip phase 1)
pytest tests/test_review_queue.py tests/test_submission.py tests/test_cost_tracker.py \
       tests/test_sop_pipeline_mode.py tests/test_criteria_checks.py -v

# Run a specific test class
pytest tests/test_review_queue.py::TestWriteOffGuard -v

# Run with coverage
pytest tests/ --cov=rcm_denial --cov-report=term-missing
```

| Test File | Count | What It Tests |
|-----------|-------|---------------|
| `test_agents.py` | — | Phase 1 agent unit tests (rule-based fallback paths) |
| `test_tools.py` | — | Phase 1 mock data tool tests |
| `test_batch_processor.py` | — | Phase 1 batch integration tests |
| `test_review_queue.py` | 15 | Enqueue, approve, re_route (valid/invalid/increment), override, write-off guard (blocked/allowed/timely_filing/force/invalid), review stats |
| `test_submission.py` | 14 | SubmissionResult/Status models, MockAdapter submit/receipt/status, adapter factory, registry CRUD, retry on transient error, permanent failure |
| `test_cost_tracker.py` | 17 | calculate_cost (all models/default/zero), record_llm_call (auto/explicit/accumulate), get_claim_cost (structure/sum/missing), get_batch_cost_summary (structure/avg/all-time) |
| `test_sop_pipeline_mode.py` | 17 | Pipeline mode toggle (on/off/global flag), manifest CRUD (read/write/upsert/overwrite/multi-payer), check_payer_coverage (all/partial/none/empty/degraded), normalize_payer_id (4 cases) |
| `test_criteria_checks.py` | 24 | DenialAnalysis (valid/invalid action/category/confidence/root_cause/consistency/reasoning), AppealLetter (valid dict/string/missing clinical/regulatory/closing/length/placeholder), EvidenceCheckResult (valid/non-bool/empty/invalid/gaps), CorrectionPlan (valid/type/code_type/empty/no-content), Golden dataset (exists/load/categories/actions/runner/failures/serialize/appeal_letter) |

---

## Future Enhancements

- **Real API integrations** — swap mock tools with FHIR / Availity / Epic endpoints
- **AWS Textract** — replace pytesseract in `eob_ocr_tool.py`
- **REST API wrapper** — FastAPI layer over `process_claim_api()` for microservice deployment
- **Parallel batch processing** — replace sequential loop with `asyncio.gather` + `Semaphore`
- **Payer portal RPA** — Playwright browser automation for portal submission
- **Alerting** ��� Prometheus alertmanager rules for write-off spikes, LLM cost anomalies
- **A/B testing** — compare model versions via golden dataset regression scores
- **RBAC** — role-based access control for review queue actions
