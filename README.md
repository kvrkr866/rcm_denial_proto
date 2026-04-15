# RCM Denial Management — Agentic AI System

**Project:** RCM - Denial Management
**Author:** RK (kvrkr866@gmail.com)
**Version:** 1.0.0

---

## The Problem

Every year, US hospitals lose **billions of dollars** in revenue due to denied insurance claims. When a payer (insurance company) denies a claim, the hospital's Revenue Cycle Management (RCM) team must:

1. **Analyze** the denial -- understand why it was denied (CARC/RARC codes)
2. **Research** payer-specific procedures -- what documentation is needed, which portal to use, filing deadlines
3. **Gather evidence** -- pull clinical records, operative reports, prior auth documentation from the EHR
4. **Decide** -- resubmit with corrections? File a formal appeal? Write off?
5. **Generate** a correction plan or appeal letter with clinical justification and regulatory basis
6. **Package** everything into a submission-ready PDF
7. **Submit** to the payer portal
8. **Track** outcomes and follow up

This process is **manual, time-consuming, and error-prone**. A single denial can take 30-60 minutes of skilled billing staff time. Multiply by thousands of denials per month, and the cost of rework often exceeds the cost of the denied claims themselves.

## The Solution

This system automates the entire denial management workflow using a **multi-agent AI pipeline** built with LangGraph. It:

- **Analyzes** denied claims using CARC/RARC codes with LLM-powered root cause analysis
- **Retrieves** payer-specific SOP procedures from a per-payer RAG knowledge base
- **Gathers** clinical evidence from EHR, EOB PDFs, patient records (5 data sources in parallel)
- **Generates** correction plans and formal appeal letters with clinical justification
- **Packages** everything into submission-ready PDFs
- **Routes** through human review with AI-generated decision summaries
- **Submits** to payer portals (mock adapters now, real API integration path ready)
- **Tracks** LLM cost, pipeline metrics, write-off revenue impact via Prometheus/Grafana

---

## System Architecture

```
 EXTERNAL DATA SOURCES                    AI PIPELINE (LangGraph)                         OUTPUTS & ACTIONS
 =====================                    =======================                         =================

 ┌─────────────────┐     ┌──────────────────────────────────────────────────────────┐
 │  Denied Claims   │     │                                                          │
 │  (CSV / Web UI)  │────►│  1. INTAKE AGENT                                         │
 │                  │     │     Validate claim fields, normalize CARC codes           │
 └─────────────────┘     │                        │                                  │
                          │                        ▼                                  │
 ┌─────────────────┐     │  2. ENRICHMENT AGENT  (parallel fan-out)                  │
 │ Patient Data     │◄───►│     ┌──────┬──────┬──────┬──────┬──────┐                 │
 │ (FHIR/PMS)      │     │     │Patient│Payer │ EHR  │ EOB  │ SOP  │                 │
 └─────────────────┘     │     │ Data  │Policy│ Docs │ OCR  │ RAG  │                 │
                          │     └──┬───┴──┬───┴──┬───┴──┬───┴──┬───┘                 │
 ┌─────────────────┐     │        │      │      │      │      │                      │
 │ Payer Policy     │◄───►│        ▼      ▼      ▼      ▼      ▼                      │
 │ (Availity/CHC)   │     │  3. ANALYSIS AGENT  (LLM + rule-based fallback)          │
 └─────────────────┘     │     Root cause analysis, CARC/RARC interpretation         │
                          │     Denial category + recommended action + confidence      │
 ┌─────────────────┐     │                        │                                  │
 │ Provider EHR     │◄───►│                 [Supervisor Router]                       │
 │ (Epic/Cerner/    │     │          ┌────────┬───┴────┬──────────┐                  │
 │  Athena FHIR)    │     │          ▼        ▼        ▼          ▼                  │
 └─────────────────┘     │     resubmit   appeal    both     write_off              │
                          │          │        │        │          │                   │
 ┌─────────────────┐     │          ▼        ▼        ▼          │                   │
 │ EOB PDFs         │◄───►│  4. EVIDENCE CHECK AGENT  (LLM call 1)                  │
 │ (OCR: PyMuPDF +  │     │     Assess evidence sufficiency, identify gaps            │
 │  Tesseract)      │     │                        │                                  │
 └─────────────────┘     │                ┌───────┴────────┐                         │
                          │                ▼                ▼                         │
 ┌─────────────────┐     │     Evidence OK?          Gaps found?                     │
 │ SOP Knowledge    │◄───►│         │                     │                          │
 │ Base (ChromaDB)  │     │         │              5. TARGETED EHR                   │
 │ Per-payer RAG    │     │         │                 AGENT                           │
 │ ┌─────────────┐ │     │         │                 Fetch labs,                     │
 │ │ SummitHealth │ │     │         │                 imaging,                        │
 │ │ NationalCare│ │     │         │                 pathology                       │
 │ │ Crestview   │ │     │         │                     │                           │
 │ │ Global SOPs │ │     │         ▼                     ▼                           │
 │ └─────────────┘ │     │  6. RESPONSE AGENT  (LLM call 2)                         │
 └─────────────────┘     │     ┌─────────────────┬────────────────┐                  │
                          │     │ CorrectionPlan  │  AppealLetter  │                  │
                          │     │ (code fixes,    │  (clinical     │                  │
                          │     │  documentation) │   justification,│                 │
                          │     │                 │   regulatory    │                  │
                          │     └────────┬────────┴───────┬────────┘                  │
                          │              ▼                ▼                            │
                          │  7. DOCUMENT PACKAGING AGENT                              │     ┌──────────────────┐
                          │     PDF generation + merge + metadata                     │────►│ Output Package    │
                          │                        │                                  │     │ ├─ Analysis.pdf   │
                          │                        ▼                                  │     │ ├─ Correction.pdf │
                          │  8. REVIEW GATE AGENT                                     │     │ ├─ Appeal.pdf     │
                          │     Build AI summary, enqueue for human review             │     │ ├─ MERGED.pdf     │
                          │                                                           │     │ ├─ metadata.json  │
                          └───────────────────────────┬───────────────────────────────┘     │ └─ audit_log.json │
                                                      │                                     └──────────────────┘
                                                      ▼
                          ┌───────────────────────────────────────────────────────────┐
                          │                    HUMAN REVIEW QUEUE                      │
                          │                                                           │
                          │   AI Summary: root cause, evidence confidence,             │
                          │   key arguments, flag reasons, recommended action          │
                          │                                                           │
                          │   Reviewer Actions:                                        │
                          │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
                          │   │ APPROVE  │ │ RE-ROUTE │ │ HUMAN    │ │  WRITE-OFF  │ │
                          │   │          │ │          │ │ OVERRIDE │ │             │ │
                          │   │ Proceed  │ │ Re-run   │ │ Replace  │ │ Last resort │ │
                          │   │ to payer │ │ from any │ │ AI output│ │ (guarded:   │ │
                          │   │ submit   │ │ pipeline │ │ with own │ │  must try   │ │
                          │   │          │ │ stage +  │ │ letter/  │ │  re-route   │ │
                          │   │          │ │ reviewer │ │ plan     │ │  first)     │ │
                          │   │          │ │ notes    │ │          │ │             │ │
                          │   └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘ │
                          └────────┼────────────┼────────────┼──────────────┼────────┘
                                   │            │            │              │
                                   │            │            │              ▼
                                   │            │            │     ┌──────────────┐
                                   │            ▼            │     │ Revenue      │
                                   │     ┌──────────────┐    │     │ Impact Log   │
                                   │     │ Re-enter     │    │     │ (write-off   │
                                   │     │ Pipeline at: │    │     │  tracking)   │
                                   │     │ - intake     │    │     └──────────────┘
                                   │     │ - ehr_fetch  │    │
                                   │     │ - response   │    │
                                   │     └──────┬───────┘    │
                                   │            │            │
                                   │            ▼            ▼
                                   │     (re-processes, lands    (re-packages with
                                   │      back in review queue)   human text, lands
                                   │                              back in review queue)
                                   ▼
                          ┌───────────────────────────────────────────────────────────┐
                          │                  PAYER SUBMISSION                          │
                          │                                                           │
                          │   Adapter per payer (from payer_submission_registry):      │
                          │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
                          │   │   Mock   │ │ Availity │ │   RPA    │ │ EDI 837  │   │
                          │   │  (test)  │ │ REST API │ │ (portal  │ │(clearinghouse)│
                          │   │          │ │          │ │ browser) │ │  SFTP)   │   │
                          │   └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
                          │                                                           │
                          │   Retry: exponential backoff (tenacity)                   │
                          │   Logging: every attempt to submission_log table           │
                          │   Status: poll payer for adjudication result               │
                          └───────────────────────────┬───────────────────────────────┘
                                                      │
                                                      ▼
                          ┌───────────────────────────────────────────────────────────┐
                          │                  OBSERVABILITY                             │
                          │                                                           │
                          │   ┌────────────┐  ┌────────────┐  ┌────────────┐         │
                          │   │ Prometheus │  │  Grafana   │  │   Loki    │         │
                          │   │ (metrics)  │  │ (dashboard)│  │  (logs)   │         │
                          │   └────────────┘  └────────────┘  └────────────┘         │
                          │                                                           │
                          │   9 metric families: claims processed, duration p50/95/99,│
                          │   LLM cost by model, submissions, review queue depth,     │
                          │   write-off revenue impact, eval quality signals           │
                          └───────────────────────────────────────────────────────────┘
```

---

## Key Features

- **10-agent LangGraph pipeline** with LLM-powered analysis and rule-based fallback (works offline)
- **Per-payer SOP RAG** -- ChromaDB knowledge base per insurance payer for payer-specific appeal guidance
- **5-source parallel enrichment** -- patient data, payer policy, EHR, EOB OCR (PyMuPDF + Tesseract), SOP RAG
- **Human-in-the-loop review** -- 4 actions: approve, re-route (to any pipeline stage), human override, write-off (guarded)
- **PDF output** -- analysis report, correction plan, appeal letter, merged submission package
- **Payer submission** -- 4 adapters (mock, Availity API, RPA portal, EDI 837) with retry + logging
- **Observability** -- Prometheus metrics, Grafana dashboard, Loki logs, LLM cost tracking
- **Evaluation** -- continuous eval from reviewer actions, 22 deterministic criteria checks, 14-case golden dataset
- **Web UI** -- NiceGUI browser interface with live pipeline progress, review queue, stats dashboard
- **Docker deployment** -- one-command `docker compose up` for app + full monitoring stack

For detailed technical architecture, design rationale, and implementation details see **[TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md)**.

---

## Quick Setup

### Option A -- Docker (Recommended)

```bash
# 1. Clone and configure
git clone <repo-url>
cd rcm_denial_proto
cp .env.example .env
# Edit .env -- set OPENAI_API_KEY (required for LLM features)

# 2. Launch everything (app + Prometheus + Grafana + Loki)
docker compose up -d

# 3. Open browser
#    Web UI:     http://localhost:8080
#    Grafana:    http://localhost:3000  (admin/admin)
#    Prometheus: http://localhost:9090
```

Run CLI commands inside the container:
```bash
docker compose exec app rcm-denial init --verify
docker compose exec app rcm-denial process-batch data/demo_denials.csv --batch-id DEMO-001
docker compose exec app rcm-denial stats --batch-id DEMO-001
docker compose exec app rcm-denial review list
```

### Option B -- Local Installation

```bash
# 1. Prerequisites: Python 3.11+, Tesseract OCR
#    Linux:  sudo apt install tesseract-ocr poppler-utils
#    macOS:  brew install tesseract poppler

# 2. Install
git clone <repo-url>
cd rcm_denial_proto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt    # all packages (core + web UI + dev tools)
pip install -e .                   # install the project itself

# 3. Configure
cp .env.example .env
# Edit .env -- set OPENAI_API_KEY

# 4. Initialize SOP knowledge base
rcm-denial init --verify

# 5. Process claims
rcm-denial process-batch data/demo_denials.csv --batch-id DEMO-001

# 6. Launch web UI
rcm-denial web
# Open http://localhost:8080
```

**Works without OpenAI key:** The system runs fully in offline/mock mode. Analysis uses rule-based CARC-map fallback, enrichment uses mock data, SOP uses keyword matching. Only appeal letter quality is reduced.

---

## Using the Web UI

### Process Claims (three-panel operator console)

Upload a CSV and watch claims process in real-time:

```
┌─────────────────┬──────────────────────────────┬────────────────────────────┐
│  PENDING (2)    │  PROCESSING NOW              │  COMPLETED (2)             │
│                 │                              │                            │
│  CLM-33003  ●   │  CLM-33001                   │  CLM-33002  ✅ complete    │
│  NC|CO-16|$8k   │  Carter Daniel | Summit      │  NationalCare | resubmit  │
│                 │  CO-252/M127 | $4,250        │  [Review] [PDF]            │
│  CLM-33004  ●   │                              │                            │
│  NC|CO-16|$3k   │  ✅ ✅ ✅ 🔄 ⬜ ⬜ ⬜         │  CLM-33001  ✅ complete    │
│                 │  Intk Enr Ana Evid Rsp Pkg   │  SummitHealth | appeal     │
│                 │                              │  [Review] [PDF]            │
│                 │  Running: evidence_check      │                            │
│                 │  Elapsed: 12.3s              │                            │
├─────────────────┴──────────────────────────────┴────────────────────────────┤
│  [Upload CSV]  Batch ID: [___]  [▶ Process All]           [Init SOPs]       │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Left panel** -- claims waiting to be processed
- **Center panel** -- currently processing claim with live pipeline stage indicators
- **Right panel** -- completed claims with Review / PDF download links

### Review Queue

Approve, re-route, override, or write-off claims with one click:

- Compact rows with key info (claim ID, CARC, amount, confidence, status)
- Action buttons with tooltips -- details expand on click
- Click claim ID to see full detail: analysis, evidence, appeal letter preview, audit trail, PDF download

### Stats Dashboard

Pipeline scorecard mirroring `rcm-denial stats`:
- Pipeline results table with duration percentiles
- LLM cost breakdown by model
- Review queue status breakdown
- Write-off revenue impact
- Eval quality signals (first-pass approval rate, override rate, confidence calibration)

### Evals

- Run golden dataset regression checks (14 cases, all 7 denial categories)
- View review-queue quality signals
- Check structural criteria on single claim output

---

## Running a Demo

### Prepare

```bash
# Place SOP documents in data/sop_documents/{payer_folder}/
# Place EOB PDFs in data/eob_pdfs/
# Create a CSV with your denied claims (see data/demo_denials.csv for format)
```

### Demo flow (all from web UI -- no CLI needed)

1. Open http://localhost:8080 -- login if auth is enabled
2. Go to **Dashboard** -- click **Clear History** to reset any previous demo data
3. Go to **Process Claims** page
4. Click **Init SOPs** -- builds RAG collections (skips if already up-to-date)
5. Upload your CSV -- claims appear in the Pending panel
6. Select claims or click **Process All** -- watch pipeline stages light up in real-time
7. Go to **Review Queue** > **Pending Review** tab -- see claims with AI summaries
8. **Approve** a claim, **re-route** another with notes, try **human override**
9. Go to **Review Queue** > **Ready to Submit** tab -- submit approved claims to payer
10. Go to **Stats** -- see operational metrics, CARC breakdown, write-off impact
11. Go to **Evals** -- run accuracy check against golden dataset
12. Click any claim ID to see full detail: submission package, audit trail, appeal letter preview

### Output structure (per claim)

```
output/CLM-33001/
├── package/                                 <-- Submitted to payer portal
│   ├── 00_cover_letter.pdf                  Cover letter (resubmission or appeal)
│   ├── 01_denial_analysis.pdf               Root cause analysis report
│   ├── 02_correction_plan.pdf               Code corrections (if resubmit)
│   ├── 03_appeal_letter.pdf                 Formal appeal (if appeal)
│   └── SUBMISSION_PACKAGE_CLM-33001.pdf     Merged bundle
│
└── internal_audit/                          <-- Internal only, NOT submitted
    ├── audit_log.json                       Processing audit trail
    └── submission_metadata.json             Claim metadata and pipeline results
```

### Sharing the demo

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for deployment options:
- **ngrok** (5 min) -- share from your machine via public URL
- **Railway.app** (10 min) -- git push to deploy, free tier
- **Render.com** (10 min) -- Docker deploy, free tier
- **AWS EC2** (15 min) -- full control, production-grade
- **Google Cloud Run** (15 min) -- serverless, auto-scales

---

## CLI Quick Reference

All operations can be done from the web UI OR the CLI. Key commands:

```bash
# Pipeline
rcm-denial process-batch data/claims.csv --batch-id BATCH-001
rcm-denial process-claim --claim-id CLM-001 --carc CO-252 --amount 4250 ...

# SOP Management
rcm-denial init --verify              # build all payer RAG collections
rcm-denial sop-status                  # show collection health

# Review Queue
rcm-denial review list --status pending
rcm-denial review approve --run-id <id>
rcm-denial review re-route --run-id <id> --stage response_agent --notes "..."
rcm-denial review override --run-id <id> --text "..."
rcm-denial review write-off --run-id <id> --reason cost_exceeds_recovery

# Submission
rcm-denial submit --run-id <id>
rcm-denial submit-batch --batch-id BATCH-001

# Statistics
rcm-denial stats --batch-id BATCH-001
rcm-denial stats --export-metrics --push-gateway http://localhost:9091

# Evaluation
rcm-denial evals run
rcm-denial evals quality-signals

# Web UI (port from WEB_PORT in .env, or override with --port)
rcm-denial web                     # uses WEB_PORT from .env (default 8080)
rcm-denial web --port 9090         # override

# Database
rcm-denial db info
rcm-denial db migrate-to-postgres
```

For the full CLI reference with all options and flags, see **[TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md)**.

---

## Configuration

Copy `.env.example` to `.env` and set your values. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | -- | Required for LLM features |
| `OPENAI_MODEL` | `gpt-4o` | LLM model |
| `WEB_PORT` | `8080` | NiceGUI web UI port (also via `--port` flag) |
| `GRAFANA_PORT` | `3000` | Grafana dashboard port (docker-compose) |
| `WEB_AUTH_ENABLED` | `false` | Enable login for web UI |
| `WEB_AUTH_USERS` | `admin:admin` | Login credentials |
| `DATABASE_TYPE` | `sqlite` | `sqlite` or `postgresql` |
| `SUBMISSION_ADAPTER` | `mock` | `mock` / `availity_api` / `rpa_portal` / `edi_837` |

For the complete configuration reference (40+ variables), see **[TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md)**.

---

## Documentation

| Document | What It Covers |
|----------|---------------|
| **[README.md](README.md)** (this file) | What, why, setup, run, demo |
| **[TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md)** | Agentic AI features, design rationale, pipeline architecture, LLM/RAG/tool details, memory/state, HITL design, eval system, observability, data models, project structure, full CLI reference, config reference, test suite, production hardening, technology stack, future improvements |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | Docker, Railway, Render, AWS, GCP deployment guides + demo script |
