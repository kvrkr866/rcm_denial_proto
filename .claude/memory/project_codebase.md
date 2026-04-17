---
name: RCM Denial Management — Codebase State
description: Full structure, architecture, key patterns, and all modules as of April 2026
type: project
---

# RCM Denial Management — Codebase State (as of 2026-04-17)

## Project Layout
```
rcm_denial_proto/
├── pyproject.toml               # entry point: rcm_denial.main:cli
├── requirements.txt             # all pip packages (core + web + dev)
├── Dockerfile                   # production container
├── docker-compose.yml           # app + Prometheus + Grafana + Loki + Pushgateway
├── .dockerignore
├── .env.example                 # full env template (40+ vars)
├── railway.json                 # Railway.app deploy config (must be in root)
├── render.yaml                  # Render.com blueprint (must be in root)
├── README.md                    # business-focused: what, why, setup, run, demo
├── rcm_denial_system_architecture.svg  # architecture diagram (to be updated)
│
├── docs/
│   ├── REQUIREMENTS_AND_ROADMAP.md    # Section A: implemented, Section B: roadmap
│   ├── architecture_diagram.md         # Mermaid diagram (renders on GitHub)
│   └── architecture_png_spec.md        # draw.io specification for PNG
│
├── deployment/
│   ├── DEPLOYMENT.md                   # all deployment options
│   ├── DEPLOY_GOOGLE_CLOUD.md          # step-by-step GCP guide
│   ├── DEPLOY_ORACLE_CLOUD.md          # step-by-step Oracle guide
│   ├── TECHNICAL_ARCHITECTURE.md       # detailed tech doc (19 sections)
│   ├── Dockerfile.hf                   # Hugging Face Spaces variant
│   ├── README_HF_SPACE.md             # HF Space metadata
│   ├── railway.json                    # copy for reference
│   └── render.yaml                     # copy for reference
│
├── src/rcm_denial/
│   ├── main.py                  # CLI (click): 30+ commands + programmatic API
│   ├── config/settings.py       # Pydantic-settings singleton (all env vars)
│   │
│   ├── models/
│   │   ├── claim.py             # ClaimRecord, EnrichedData, PatientData, PayerPolicy,
│   │   │                        #   EhrData, EobExtractedData, SopResult, DiagnosticReport
│   │   ├── analysis.py          # DenialAnalysis, EvidenceCheckResult, CorrectionPlan
│   │   ├── appeal.py            # AppealPackage, AppealLetter, SupportingDocument
│   │   ├── output.py            # DenialWorkflowState, SubmissionPackage, AuditEntry
│   │   └── submission.py        # SubmissionResult, SubmissionStatus
│   │
│   ├── agents/                  # 10 LangGraph agent nodes
│   │   ├── intake_agent.py
│   │   ├── enrichment_agent.py       # async fan-out to 5 tools
│   │   ├── analysis_agent.py         # LLM + rule-based CARC fallback
│   │   ├── evidence_check_agent.py   # LLM call 1 + rate limiter + cost tracker
│   │   ├── targeted_ehr_agent.py     # stage 2 EHR fetch
│   │   ├── response_agent.py         # LLM call 2 + rate limiter + cost tracker
│   │   ├── correction_plan_agent.py
│   │   ├── appeal_prep_agent.py
│   │   ├── document_packaging_agent.py  # cover letter + PDFs → package/ + internal_audit/
│   │   └── review_gate_agent.py
│   │
│   ├── tools/
│   │   ├── patient_data_tool.py      # mock → FHIR R4
│   │   ├── payer_policy_tool.py      # mock → Availity
│   │   ├── ehr_tool.py               # mock → Epic/Cerner
│   │   ├── clinical_ocr_tool.py
│   │   ├── eob_ocr_tool.py           # PyMuPDF primary + Tesseract fallback
│   │   └── sop_rag_tool.py           # per-payer ChromaDB RAG + pipeline mode + keyword fallback
│   │
│   ├── workflows/
│   │   ├── denial_graph.py           # LangGraph StateGraph + _wrap_node + checkpointing
│   │   ├── batch_processor.py        # batch engine + SOP pre-flight + metrics push
│   │   └── supervisor_router.py
│   │
│   ├── services/
│   │   ├── audit_service.py          # structlog
│   │   ├── pdf_service.py            # reportlab + pypdf + cover letter
│   │   ├── claim_intake.py           # CSV parsing + flexible date validator + DB init
│   │   ├── data_source_adapters.py   # BaseEMR/PMS/PayerAdapter + mocks + factories
│   │   ├── sop_ingestion.py          # per-payer indexing + manifest + skip-if-fresh
│   │   ├── review_queue.py           # queue actions + get_queue_count + eval stats
│   │   ├── review_queue_helpers.py
│   │   ├── pipeline_reentry.py
│   │   ├── submission_adapters.py    # Mock/Availity/RPA/EDI + payer registry
│   │   ├── submission_service.py     # retry + logging
│   │   ├── cost_tracker.py           # per-call LLM cost
│   │   ├── metrics_service.py        # .prom export + Pushgateway push (all-time cumulative)
│   │   ├── db_backend.py             # SQLite/PostgreSQL factory
│   │   ├── checkpoint_service.py     # per-node crash recovery
│   │   ├── rate_limiter.py           # token bucket
│   │   ├── claim_disposition.py      # post-submission disposition + EHR sync
│   │   └── data_cleanup.py           # Clear History (preserves RAG)
│   │
│   ├── evaluation/evaluator.py       # 5-metric LLM-as-judge
│   ├── evals/criteria_checks.py      # 22 deterministic checks + golden runner
│   │
│   └── web/                          # NiceGUI (separate from backend)
│       ├── app.py                    # entry point
│       ├── layout.py                 # shared header/footer (avoids circular import)
│       ├── auth.py                   # login/logout
│       └── pages/
│           ├── dashboard.py          # overview + Clear History
│           ├── process.py            # 3-panel console + session state preservation
│           ├── review.py             # 3 tabs: Pending/Ready to Submit/Submitted
│           ├── claim_detail.py       # full view + package vs internal_audit
│           ├── stats.py              # operational metrics (no LLM cost)
│           └── evals.py              # accuracy check + quality signals
│
├── data/
│   ├── sample_denials.csv            # 5 sample claims
│   ├── demo_denials.csv              # 12 real claims across 3 payers
│   ├── carc_rarc_reference.json
│   ├── sop_documents/
│   │   ├── global/                   # 4 generic SOPs (.txt)
│   │   ├── summithealth/             # Summit_Health.pdf
│   │   ├── nationalcare/             # NationalCare.pdf
│   │   ├── crestviewhealth/          # Crestview_Health.pdf
│   │   └── manifest.json             # collection health tracker
│   ├── eob_pdfs/                     # 4+ EOB PDF files
│   ├── evals/golden_cases.json       # 14 labeled cases
│   └── observability/
│       ├── prometheus.yml            # scrapes pushgateway:9091
│       ├── promtail_config.yaml      # ships to loki:3100, reads /app/logs/
│       ├── grafana_dashboard.json    # 21 panels + 3 Loki log panels
│       └── grafana_provisioning/     # datasources (prometheus + loki) + dashboard
│
├── tests/                            # 9 test files, 110+ tests
│   ├── conftest.py                   # shared fixtures with all DB tables
│   ├── test_review_queue.py (15)
│   ├── test_submission.py (14)
│   ├── test_cost_tracker.py (17)
│   ├── test_sop_pipeline_mode.py (17)
│   ├── test_criteria_checks.py (24)
│   ├── test_rate_limiter.py (6)
│   └── (+ 3 phase 1 test files)
│
├── .github/workflows/ci.yml         # lint + test + Docker build
└── output/                           # per-claim: package/ + internal_audit/

```

## Pipeline Topology (updated)
```
START → intake → enrichment(5 tools ∥) → analysis(LLM+fallback)
    → [supervisor: resubmit|appeal|both|write_off]
    → evidence_check(LLM1) → [if gaps: targeted_ehr] → response(LLM2)
    → document_packaging(cover letter + PDFs → package/ + internal_audit/)
    → review_gate(AI summary → human_review_queue)
    → [HITL: approve|re_route|override|write_off(guarded)]
    → [if approved: payer_submission → claim_disposition → ehr_sync]
```

## Key Technical Notes
- **Circular import fix**: layout.py holds create_header/create_footer (not app.py)
- **NiceGUI version difference**: local=2.4.0 (e.content), Docker=3.10.0 (e.file async)
- **Settings import gotcha**: `from rcm_denial.config import settings` gets the INSTANCE (not module) due to __init__.py re-export. Use `sys.modules['rcm_denial.config.settings']` for the actual module.
- **Pushgateway**: push all-time cumulative metrics (not batch-specific) so Grafana matches NiceGUI
- **push_to_gateway call**: must use `_push(gateway=url, job=job, ...)` — gateway is first positional param
- **SOP PDF loading**: uses PyMuPDF (fitz) in sop_rag_tool.py, NOT pdf2image/poppler
- **Date parsing**: flexible validator in claim_intake.py handles DD-MM-YYYY, YYYY-MM-DD, etc.
- **Output structure**: output/{claim_id}/package/ (payer) + internal_audit/ (not submitted)
- **Clear History**: preserves RAG KB and manifest.json
